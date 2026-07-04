"""
RyzenSense Hardware Tuner
Applies CPU and GPU tuning settings via ryzenadj, amdsmi, and sysfs.
"""

import subprocess
import os
import sys
import platform
import json
from typing import Optional


class HardwareTuner:
    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.platform = platform.system()
        self._ryzenadj_path = self._find_ryzenadj()
        self._amdsmi_available = self._check_amdsmi()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def apply(self, settings: dict) -> bool:
        """Apply all settings from an AI-generated tuning command."""
        success = True

        cpu_settings = settings.get("cpu", {})
        gpu_settings = settings.get("gpu", {})
        fan_settings = settings.get("fan", {})

        if cpu_settings:
            print("  → Applying CPU settings...")
            ok = self._apply_cpu(cpu_settings)
            status = "✓" if ok else "✗"
            print(f"    [{status}] CPU TDP: {cpu_settings.get('stapm_limit_w')}W sustained / "
                  f"{cpu_settings.get('fast_limit_w')}W boost | "
                  f"Thermal limit: {cpu_settings.get('tctl_temp_c')}°C")
            success = success and ok

        if gpu_settings:
            print("  → Applying GPU settings...")
            ok = self._apply_gpu(gpu_settings)
            status = "✓" if ok else "✗"
            print(f"    [{status}] GPU power: {gpu_settings.get('power_limit_w')}W | "
                  f"Clock: {gpu_settings.get('clock_limit_mhz') or 'default'} MHz | "
                  f"Fan: {gpu_settings.get('fan_mode', 'auto')}")
            success = success and ok

        if fan_settings and fan_settings.get("mode") == "custom":
            print("  → Applying fan settings...")
            ok = self._apply_fan(fan_settings)
            status = "✓" if ok else "✗"
            print(f"    [{status}] Fan speed: {fan_settings.get('speed_percent')}%")

        if self.dry_run:
            print("\n  [DRY RUN] No hardware was actually changed.")

        return success

    def reset_to_defaults(self) -> bool:
        """Reset CPU and GPU to firmware defaults."""
        defaults = {
            "cpu": {
                "stapm_limit_w": 45,
                "fast_limit_w": 54,
                "slow_limit_w": 45,
                "tctl_temp_c": 90,
            },
            "gpu": {
                "power_limit_w": None,
                "clock_limit_mhz": None,
                "fan_mode": "auto",
            },
            "fan": {"mode": "auto", "speed_percent": None},
        }
        print("[*] Resetting to firmware defaults...")
        return self.apply(defaults)

    # ──────────────────────────────────────────────────────────────────────────
    # CPU Tuning via ryzenadj
    # ──────────────────────────────────────────────────────────────────────────

    def _apply_cpu(self, cpu: dict) -> bool:
        stapm = cpu.get("stapm_limit_w")
        fast = cpu.get("fast_limit_w")
        slow = cpu.get("slow_limit_w")
        tctl = cpu.get("tctl_temp_c")

        if not any([stapm, fast, slow, tctl]):
            return True  # Nothing to apply

        # Safety clamps
        if tctl and tctl > 95:
            print(f"    [!] tctl_temp_c {tctl}°C clamped to 95°C for safety")
            tctl = 95
        if stapm and stapm > 200:
            print(f"    [!] stapm_limit_w {stapm}W seems unrealistic, clamping to 200W")
            stapm = 200

        if self._ryzenadj_path:
            return self._apply_cpu_ryzenadj(stapm, fast, slow, tctl)

        if self.platform == "Linux":
            return self._apply_cpu_sysfs(stapm, tctl)

        if self.platform == "Windows":
            return self._apply_cpu_windows(stapm, tctl)

        print("    [!] No CPU tuning backend available (ryzenadj not found)")
        return False

    def _apply_cpu_ryzenadj(self, stapm: Optional[int], fast: Optional[int],
                             slow: Optional[int], tctl: Optional[int]) -> bool:
        args = [self._ryzenadj_path]

        if stapm:
            args += [f"--stapm-limit={stapm * 1000}"]   # W → mW
        if fast:
            args += [f"--fast-limit={fast * 1000}"]
        if slow:
            args += [f"--slow-limit={slow * 1000}"]
        if tctl:
            args += [f"--tctl-temp={tctl * 1000}"]       # °C → m°C

        return self._run(args, "ryzenadj")

    def _apply_cpu_sysfs(self, tdp: Optional[int], tctl: Optional[int]) -> bool:
        """Fallback: write to /sys/devices/system/cpu/cpufreq for scaling."""
        ok = True
        if tdp:
            # AMD RAPL power limit via powercap
            rapl_paths = [
                "/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw",
            ]
            for p in rapl_paths:
                if os.path.exists(p):
                    microwatts = tdp * 1_000_000
                    ok = ok and self._write_sysfs(p, str(microwatts))
        return ok

    def _apply_cpu_windows(self, tdp: Optional[int], tctl: Optional[int]) -> bool:
        """Windows: use powercfg to set processor power caps."""
        if not tdp:
            return True
        # Convert W to % of max (approximate — 65W base assumed)
        pct = min(100, max(10, int((tdp / 65) * 100)))
        return self._run(
            ["powercfg", "/SETACVALUEINDEX", "SCHEME_CURRENT",
             "54533251-82be-4824-96c1-47b60b740d00",
             "bc5038f7-23e0-4960-96da-33abaf5935ec", str(pct)],
            "powercfg"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # GPU Tuning via amdsmi
    # ──────────────────────────────────────────────────────────────────────────

    def _apply_gpu(self, gpu: dict) -> bool:
        power_limit = gpu.get("power_limit_w")
        clock_limit = gpu.get("clock_limit_mhz")
        fan_mode = gpu.get("fan_mode", "auto")

        if not self._amdsmi_available:
            # Try sysfs fallback for fan at least
            if fan_mode == "max":
                self._set_fan_sysfs(100)
            elif fan_mode == "silent":
                self._set_fan_sysfs(30)
            elif fan_mode == "auto":
                self._set_fan_sysfs_auto()
            return True  # Soft failure — log but don't block

        try:
            import amdsmi
            devices = amdsmi.amdsmi_get_processor_handles()
            if not devices:
                return False
            dev = devices[0]

            # Power limit
            if power_limit:
                if not self.dry_run:
                    amdsmi.amdsmi_set_power_cap(dev, 0, power_limit * 1_000_000)  # W → µW

            # Clock limit
            if clock_limit:
                if not self.dry_run:
                    amdsmi.amdsmi_set_gpu_clk_range(
                        dev,
                        amdsmi.AmdSmiClkType.GFX,
                        0,
                        clock_limit
                    )

            # Fan control
            self._apply_gpu_fan_amdsmi(dev, fan_mode, gpu.get("fan_speed_percent"))
            return True

        except Exception as e:
            print(f"    [!] amdsmi GPU tuning error: {e}")
            return False

    def _apply_gpu_fan_amdsmi(self, dev, mode: str, speed_pct: Optional[int]):
        try:
            import amdsmi
            if mode == "auto":
                amdsmi.amdsmi_reset_gpu_fan_speed(dev, 0)
            elif mode == "max":
                if not self.dry_run:
                    max_speed = amdsmi.amdsmi_get_gpu_fan_speed_max(dev, 0)
                    amdsmi.amdsmi_set_gpu_fan_speed(dev, 0, max_speed)
            elif mode == "silent":
                if not self.dry_run:
                    max_speed = amdsmi.amdsmi_get_gpu_fan_speed_max(dev, 0)
                    amdsmi.amdsmi_set_gpu_fan_speed(dev, 0, int(max_speed * 0.30))
            elif mode == "balanced":
                if not self.dry_run:
                    max_speed = amdsmi.amdsmi_get_gpu_fan_speed_max(dev, 0)
                    amdsmi.amdsmi_set_gpu_fan_speed(dev, 0, int(max_speed * 0.55))
            elif mode == "custom" and speed_pct is not None:
                if not self.dry_run:
                    max_speed = amdsmi.amdsmi_get_gpu_fan_speed_max(dev, 0)
                    amdsmi.amdsmi_set_gpu_fan_speed(dev, 0, int(max_speed * speed_pct / 100))
        except Exception as e:
            print(f"    [!] Fan control error: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Fan (sysfs fallback)
    # ──────────────────────────────────────────────────────────────────────────

    def _apply_fan(self, fan: dict) -> bool:
        mode = fan.get("mode", "auto")
        speed = fan.get("speed_percent")
        if mode == "custom" and speed:
            return self._set_fan_sysfs(speed)
        elif mode == "auto":
            return self._set_fan_sysfs_auto()
        elif mode == "max":
            return self._set_fan_sysfs(100)
        elif mode == "silent":
            return self._set_fan_sysfs(25)
        return True

    def _set_fan_sysfs(self, pct: int) -> bool:
        """Write fan speed to hwmon sysfs."""
        for hwmon in self._iter_hwmon():
            pwm_path = os.path.join("/sys/class/hwmon", hwmon, "pwm1")
            enable_path = os.path.join("/sys/class/hwmon", hwmon, "pwm1_enable")
            if os.path.exists(pwm_path):
                pwm_val = int(pct / 100 * 255)
                ok = self._write_sysfs(enable_path, "1")
                ok = ok and self._write_sysfs(pwm_path, str(pwm_val))
                return ok
        return False

    def _set_fan_sysfs_auto(self) -> bool:
        for hwmon in self._iter_hwmon():
            enable_path = os.path.join("/sys/class/hwmon", hwmon, "pwm1_enable")
            if os.path.exists(enable_path):
                return self._write_sysfs(enable_path, "2")
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Utilities
    # ──────────────────────────────────────────────────────────────────────────

    def _run(self, args: list, name: str) -> bool:
        if self.dry_run:
            print(f"    [DRY] {' '.join(str(a) for a in args)}")
            return True
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                print(f"    [!] {name} error: {result.stderr.strip()[:200]}")
                return False
            return True
        except FileNotFoundError:
            print(f"    [!] {name} not found at {args[0]}")
            return False
        except subprocess.TimeoutExpired:
            print(f"    [!] {name} timed out")
            return False

    def _write_sysfs(self, path: str, value: str) -> bool:
        if self.dry_run:
            print(f"    [DRY] echo {value} > {path}")
            return True
        try:
            with open(path, "w") as f:
                f.write(value)
            return True
        except PermissionError:
            print(f"    [!] Permission denied writing {path} (need root?)")
            return False
        except Exception as e:
            print(f"    [!] Failed writing {path}: {e}")
            return False

    def _iter_hwmon(self):
        if self.platform != "Linux":
            return
        try:
            for hwmon in os.listdir("/sys/class/hwmon"):
                yield hwmon
        except Exception:
            return

    def _find_ryzenadj(self) -> Optional[str]:
        candidates = [
            "ryzenadj",
            "/usr/bin/ryzenadj",
            "/usr/local/bin/ryzenadj",
            r"C:\Program Files\RyzenAdj\ryzenadj.exe",
        ]
        for c in candidates:
            try:
                result = subprocess.run([c, "--help"], capture_output=True, timeout=2)
                return c
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
        return None

    def _check_amdsmi(self) -> bool:
        try:
            import amdsmi
            return True
        except ImportError:
            return False