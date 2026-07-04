"""
RyzenSense v2 - Watchdog Agent
Monitors telemetry in real-time and automatically triggers tuning
when thresholds are breached. Detects workload changes too.
"""

import time
from collections import deque
from agents.base_agent import BaseAgent


# Thermal thresholds
CPU_TEMP_WARNING = 80    # °C — request cooling tune
CPU_TEMP_CRITICAL = 90   # °C — emergency cool
GPU_TEMP_WARNING = 82    # °C
GPU_TEMP_CRITICAL = 92   # °C

# Workload detection thresholds
GAMING_GPU_USAGE = 70    # GPU > 70% = likely gaming
RENDER_CPU_USAGE = 85    # CPU > 85% sustained = rendering
IDLE_CPU_USAGE = 10      # CPU < 10% sustained = idle


class WatchdogAgent(BaseAgent):
    """
    Always-on monitoring agent.
    Watches for thermal emergencies and workload shifts,
    then automatically requests tuning adjustments.
    """

    CHECK_INTERVAL = 2.0  # seconds between checks

    def __init__(self):
        super().__init__()
        self._last_workload = "unknown"
        self._last_tune_time = 0
        self._tune_cooldown = 30  # seconds between auto-tunes
        self._alert_history = deque(maxlen=20)
        self._sustained_usage = {
            "cpu": deque(maxlen=10),  # 10 samples = 10s
            "gpu": deque(maxlen=10),
        }
        self._warnings_issued = set()

    def run(self):
        self._running = True
        self.log("Started — watching for thermal events and workload changes")

        while self._running:
            time.sleep(self.CHECK_INTERVAL)

        self.log("Stopped.")

    def on_event(self, event: str, data: dict, sender: str):
        """React to telemetry updates from TelemetryAgent."""
        if event != "telemetry_update":
            return

        state = data.get("state", {})
        trends = data.get("trends", {})

        cpu = state.get("cpu", {})
        gpu = state.get("gpu", {})

        cpu_temp = cpu.get("temp_c", 0)
        gpu_temp = gpu.get("temp_c", 0)
        cpu_usage = cpu.get("usage_percent", 0)
        gpu_usage = gpu.get("usage_percent", 0)

        # Update sustained usage trackers
        self._sustained_usage["cpu"].append(cpu_usage)
        self._sustained_usage["gpu"].append(gpu_usage)

        # 1. Check thermal emergencies
        self._check_thermals(cpu_temp, gpu_temp, state)

        # 2. Detect workload changes
        self._detect_workload(cpu_usage, gpu_usage, state)

        # 3. Battery protection
        self._check_battery(state)

        self.set_action(
            f"watching | CPU {cpu_temp}°C {trends.get('cpu_temp_trend','?')} | "
            f"GPU {gpu_temp}°C {trends.get('gpu_temp_trend','?')}"
        )

    def _check_thermals(self, cpu_temp: float, gpu_temp: float, state: dict):
        """Trigger cooling actions on thermal events."""
        now = time.time()
        cooldown_ok = (now - self._last_tune_time) > self._tune_cooldown

        # Critical CPU temp — emergency
        if cpu_temp >= CPU_TEMP_CRITICAL:
            self._alert("CRITICAL", f"CPU temp {cpu_temp}°C — emergency cooling!")
            self.broadcast("emergency_cool", {
                "reason": f"CPU critical temp {cpu_temp}°C",
                "state": state,
            })
            self._last_tune_time = now

        # Warning CPU temp — request softer tune
        elif cpu_temp >= CPU_TEMP_WARNING and cooldown_ok:
            if "cpu_warn" not in self._warnings_issued:
                self._alert("WARNING", f"CPU temp {cpu_temp}°C — requesting thermal tune")
                self.broadcast("tune_request", {
                    "goal": f"reduce CPU temperature, currently {cpu_temp}°C, keep under 80°C",
                    "source": "watchdog_thermal",
                    "state": state,
                })
                self._warnings_issued.add("cpu_warn")
                self._last_tune_time = now
        else:
            self._warnings_issued.discard("cpu_warn")

        # Critical GPU temp
        if gpu_temp >= GPU_TEMP_CRITICAL:
            self._alert("CRITICAL", f"GPU temp {gpu_temp}°C — emergency cooling!")
            self.broadcast("emergency_cool", {
                "reason": f"GPU critical temp {gpu_temp}°C",
                "state": state,
            })
            self._last_tune_time = now

        elif gpu_temp >= GPU_TEMP_WARNING and cooldown_ok:
            if "gpu_warn" not in self._warnings_issued:
                self._alert("WARNING", f"GPU temp {gpu_temp}°C — requesting fan boost")
                self.broadcast("tune_request", {
                    "goal": f"GPU is at {gpu_temp}°C, boost fan speed and reduce GPU power",
                    "source": "watchdog_gpu_thermal",
                    "state": state,
                })
                self._warnings_issued.add("gpu_warn")
                self._last_tune_time = now
        else:
            self._warnings_issued.discard("gpu_warn")

    def _detect_workload(self, cpu_usage: float, gpu_usage: float, state: dict):
        """Detect workload type and suggest profile changes."""
        now = time.time()
        cooldown_ok = (now - self._last_tune_time) > self._tune_cooldown * 2

        if len(self._sustained_usage["cpu"]) < 8:
            return  # Not enough data yet

        avg_cpu = sum(self._sustained_usage["cpu"]) / len(self._sustained_usage["cpu"])
        avg_gpu = sum(self._sustained_usage["gpu"]) / len(self._sustained_usage["gpu"])

        # Detect workload
        if avg_gpu > GAMING_GPU_USAGE and avg_cpu > 30:
            detected = "gaming"
        elif avg_cpu > RENDER_CPU_USAGE and avg_gpu < 30:
            detected = "rendering"
        elif avg_cpu < IDLE_CPU_USAGE and avg_gpu < 10:
            detected = "idle"
        else:
            detected = "general"

        # If workload changed, suggest a tune
        if detected != self._last_workload and cooldown_ok and detected != "unknown":
            self.log(f"Workload change detected: {self._last_workload} → {detected}")
            self._last_workload = detected

            goal_map = {
                "gaming": "gaming workload detected — optimize for maximum FPS",
                "rendering": "heavy CPU rendering detected — maximize sustained CPU performance",
                "idle": "system is idle — switch to power saving mode",
                "general": "mixed workload — use balanced profile",
            }

            self.broadcast("tune_request", {
                "goal": goal_map[detected],
                "source": f"watchdog_workload:{detected}",
                "state": state,
                "auto": True,
            })
            self._last_tune_time = now

    def _check_battery(self, state: dict):
        """Warn on low battery and suggest battery saver."""
        bat = state.get("battery", {})
        pct = bat.get("percent", 100)
        plugged = bat.get("plugged_in", True)

        if not plugged:
            if pct <= 15 and "bat_critical" not in self._warnings_issued:
                self._alert("WARNING", f"Battery critical: {pct}%")
                self.broadcast("tune_request", {
                    "goal": f"battery critical at {pct}%, maximize battery life now",
                    "source": "watchdog_battery",
                    "state": state,
                })
                self._warnings_issued.add("bat_critical")
            elif pct <= 30 and "bat_low" not in self._warnings_issued:
                self._alert("INFO", f"Battery low: {pct}%")
                self._warnings_issued.add("bat_low")
            elif pct > 35:
                self._warnings_issued.discard("bat_low")
                self._warnings_issued.discard("bat_critical")

    def _alert(self, level: str, msg: str):
        icons = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🔵"}
        icon = icons.get(level, "•")
        self.log(f"{icon} [{level}] {msg}")
        self._alert_history.append({
            "level": level,
            "message": msg,
            "time": time.strftime("%H:%M:%S"),
        })

    def get_alerts(self) -> list:
        return list(self._alert_history)