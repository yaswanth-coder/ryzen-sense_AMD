"""
RyzenSense Profile Manager
Handles saving, loading, and history of tuning sessions and profiles.
"""

import json
import os
import sqlite3
from datetime import datetime
from typing import Optional


PROFILES_DIR = os.path.join(os.path.dirname(__file__), "presets")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "history.db")

# Built-in preset profiles
BUILTIN_PRESETS = {
    "gaming": {
        "profile_name": "gaming",
        "reasoning": "Maximum CPU and GPU performance for gaming workloads.",
        "cpu": {"stapm_limit_w": 65, "fast_limit_w": 78, "slow_limit_w": 65, "tctl_temp_c": 88},
        "gpu": {"power_limit_w": 180, "clock_limit_mhz": None, "fan_mode": "auto"},
        "fan": {"mode": "auto", "speed_percent": None},
        "expected_outcome": "Maximum FPS, higher temps and fan noise expected."
    },
    "silent": {
        "profile_name": "silent",
        "reasoning": "Minimise noise and heat for meetings or quiet environments.",
        "cpu": {"stapm_limit_w": 15, "fast_limit_w": 18, "slow_limit_w": 15, "tctl_temp_c": 75},
        "gpu": {"power_limit_w": 50, "clock_limit_mhz": 1200, "fan_mode": "silent"},
        "fan": {"mode": "silent", "speed_percent": None},
        "expected_outcome": "Very quiet operation, reduced performance."
    },
    "balanced": {
        "profile_name": "balanced",
        "reasoning": "Balance between performance and thermals.",
        "cpu": {"stapm_limit_w": 45, "fast_limit_w": 54, "slow_limit_w": 45, "tctl_temp_c": 85},
        "gpu": {"power_limit_w": 120, "clock_limit_mhz": None, "fan_mode": "balanced"},
        "fan": {"mode": "balanced", "speed_percent": None},
        "expected_outcome": "Good performance with moderate temps."
    },
    "battery": {
        "profile_name": "battery",
        "reasoning": "Maximise battery life on laptops.",
        "cpu": {"stapm_limit_w": 10, "fast_limit_w": 12, "slow_limit_w": 10, "tctl_temp_c": 70},
        "gpu": {"power_limit_w": 30, "clock_limit_mhz": 800, "fan_mode": "silent"},
        "fan": {"mode": "silent", "speed_percent": None},
        "expected_outcome": "Extended battery life, significantly reduced performance."
    },
    "rendering": {
        "profile_name": "rendering",
        "reasoning": "Sustained max CPU/GPU for long render jobs.",
        "cpu": {"stapm_limit_w": 95, "fast_limit_w": 95, "slow_limit_w": 95, "tctl_temp_c": 90},
        "gpu": {"power_limit_w": 220, "clock_limit_mhz": None, "fan_mode": "max"},
        "fan": {"mode": "max", "speed_percent": None},
        "expected_outcome": "Maximum sustained throughput. Loud fans."
    },
}


class ProfileManager:
    def __init__(self):
        os.makedirs(PROFILES_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._init_db()
        self._write_builtin_presets()

    # ──────────────────────────────────────────────────────────────────────────
    # Profile CRUD
    # ──────────────────────────────────────────────────────────────────────────

    def list_profiles(self):
        """Print all available profiles (built-in + custom)."""
        print("\n── Available Profiles ──────────────────────────")
        print(f"{'Name':<18} {'Type':<10} {'Description'}")
        print("─" * 60)

        for name, preset in BUILTIN_PRESETS.items():
            print(f"  {name:<16} {'built-in':<10} {preset['expected_outcome'][:45]}")

        custom = self._list_custom_profiles()
        for name in custom:
            print(f"  {name:<16} {'custom':<10}")

        print(f"\nUse: python main.py --profile apply <name>")

    def apply_profile(self, name: str, tuner):
        """Apply a named profile."""
        settings = self._load_profile(name)
        if not settings:
            print(f"[!] Profile '{name}' not found. Run --profile list to see options.")
            return

        print(f"\n[*] Applying profile: {name}")
        tuner.apply(settings)
        print(f"[✓] Profile '{name}' applied.")

    def save_current_as(self, name: str, current_state: dict):
        """Save the current hardware state as a named profile."""
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        data = {
            "profile_name": name,
            "saved_at": datetime.now().isoformat(),
            "snapshot": current_state,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[✓] Profile '{name}' saved to {path}")

    def delete_profile(self, name: str):
        """Delete a custom profile."""
        if name in BUILTIN_PRESETS:
            print(f"[!] Cannot delete built-in profile '{name}'.")
            return
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        if os.path.exists(path):
            os.remove(path)
            print(f"[✓] Profile '{name}' deleted.")
        else:
            print(f"[!] Profile '{name}' not found.")

    # ──────────────────────────────────────────────────────────────────────────
    # Session Logging
    # ──────────────────────────────────────────────────────────────────────────

    def log_session(self, goal: str, state: dict, settings: dict):
        """Log a tuning session to SQLite history."""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    INSERT INTO sessions
                        (timestamp, goal, profile_name, cpu_temp_before,
                         gpu_temp_before, tdp_applied_w, settings_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(),
                    goal,
                    settings.get("profile_name", "custom"),
                    state["cpu"]["temp_c"],
                    state["gpu"]["temp_c"],
                    settings.get("cpu", {}).get("stapm_limit_w"),
                    json.dumps(settings),
                ))
        except Exception as e:
            print(f"    [!] Could not log session: {e}")

    def log_benchmark(self, data: dict):
        """Log benchmark results."""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    INSERT INTO benchmarks
                        (timestamp, single_thread_s, multi_thread_s,
                         mt_speedup, cpu_temp_before, cpu_temp_after)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    data["timestamp"],
                    data["single_thread_s"],
                    data["multi_thread_s"],
                    data["mt_speedup"],
                    data["cpu_temp_before"],
                    data["cpu_temp_after"],
                ))
        except Exception as e:
            print(f"    [!] Could not log benchmark: {e}")

    def show_history(self, limit: int = 20):
        """Print recent tuning history."""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute("""
                    SELECT timestamp, goal, profile_name, tdp_applied_w,
                           cpu_temp_before, gpu_temp_before
                    FROM sessions
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,)).fetchall()

            if not rows:
                print("[*] No tuning history yet.")
                return

            print("\n── Tuning History ──────────────────────────────────────────────")
            print(f"{'Time':<21} {'Profile':<12} {'TDP':<8} {'CPU°':<7} {'Goal'}")
            print("─" * 70)
            for ts, goal, profile, tdp, cpu_t, gpu_t in rows:
                ts_short = ts[:19]
                print(f"  {ts_short}  {(profile or 'custom'):<12} {(str(tdp)+'W'):<8} "
                      f"{str(cpu_t)+'°C':<7} {goal[:35]}")
        except Exception as e:
            print(f"[!] Could not read history: {e}")

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────

    def _load_profile(self, name: str) -> Optional[dict]:
        if name in BUILTIN_PRESETS:
            return BUILTIN_PRESETS[name]
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return None

    def _list_custom_profiles(self) -> list:
        names = []
        for f in os.listdir(PROFILES_DIR):
            if f.endswith(".json") and not f.startswith("_"):
                names.append(f[:-5])
        return names

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    goal TEXT,
                    profile_name TEXT,
                    cpu_temp_before REAL,
                    gpu_temp_before REAL,
                    tdp_applied_w INTEGER,
                    settings_json TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS benchmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    single_thread_s REAL,
                    multi_thread_s REAL,
                    mt_speedup REAL,
                    cpu_temp_before REAL,
                    cpu_temp_after REAL
                )
            """)

    def _write_builtin_presets(self):
        """Write built-in presets as JSON files for reference."""
        for name, data in BUILTIN_PRESETS.items():
            path = os.path.join(PROFILES_DIR, f"_{name}.json")
            if not os.path.exists(path):
                with open(path, "w") as f:
                    json.dump(data, f, indent=2)