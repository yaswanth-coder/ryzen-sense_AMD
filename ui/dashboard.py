"""
RyzenSense v2 - Profile Agent
Manages tuning profiles, learns from past sessions,
and recommends the best profile based on context.
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional
from google import genai
from agents.base_agent import BaseAgent


PROFILES_DIR = os.path.join(os.path.dirname(__file__), "..", "profiles", "presets")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "history.db")

BUILTIN_PRESETS = {
    "gaming": {
        "profile_name": "gaming",
        "reasoning": "Maximum CPU and GPU performance for gaming.",
        "cpu": {"stapm_limit_w": 65, "fast_limit_w": 78, "slow_limit_w": 65, "tctl_temp_c": 88},
        "gpu": {"power_limit_w": 180, "clock_limit_mhz": None, "fan_mode": "auto"},
        "fan": {"mode": "auto", "speed_percent": None},
        "expected_outcome": "Maximum FPS. Higher temps and fan noise expected.",
        "confidence": 0.95, "warnings": [],
    },
    "silent": {
        "profile_name": "silent",
        "reasoning": "Minimize noise and heat for quiet environments.",
        "cpu": {"stapm_limit_w": 15, "fast_limit_w": 18, "slow_limit_w": 15, "tctl_temp_c": 75},
        "gpu": {"power_limit_w": 50, "clock_limit_mhz": 1200, "fan_mode": "silent"},
        "fan": {"mode": "silent", "speed_percent": None},
        "expected_outcome": "Very quiet. Reduced performance.",
        "confidence": 0.95, "warnings": [],
    },
    "balanced": {
        "profile_name": "balanced",
        "reasoning": "Balance between performance and thermals.",
        "cpu": {"stapm_limit_w": 45, "fast_limit_w": 54, "slow_limit_w": 45, "tctl_temp_c": 85},
        "gpu": {"power_limit_w": 120, "clock_limit_mhz": None, "fan_mode": "balanced"},
        "fan": {"mode": "balanced", "speed_percent": None},
        "expected_outcome": "Good everyday performance.",
        "confidence": 0.95, "warnings": [],
    },
    "battery": {
        "profile_name": "battery",
        "reasoning": "Maximize battery life on laptops.",
        "cpu": {"stapm_limit_w": 10, "fast_limit_w": 12, "slow_limit_w": 10, "tctl_temp_c": 70},
        "gpu": {"power_limit_w": 30, "clock_limit_mhz": 800, "fan_mode": "silent"},
        "fan": {"mode": "silent", "speed_percent": None},
        "expected_outcome": "Extended battery life. Significantly reduced performance.",
        "confidence": 0.95, "warnings": [],
    },
    "rendering": {
        "profile_name": "rendering",
        "reasoning": "Sustained max CPU/GPU for long render jobs.",
        "cpu": {"stapm_limit_w": 95, "fast_limit_w": 95, "slow_limit_w": 95, "tctl_temp_c": 90},
        "gpu": {"power_limit_w": 220, "clock_limit_mhz": None, "fan_mode": "max"},
        "fan": {"mode": "max", "speed_percent": None},
        "expected_outcome": "Maximum sustained throughput. Loud fans.",
        "confidence": 0.95, "warnings": [],
    },
    "emergency_cool": {
        "profile_name": "emergency_cool",
        "reasoning": "Emergency thermal protection.",
        "cpu": {"stapm_limit_w": 8, "fast_limit_w": 10, "slow_limit_w": 8, "tctl_temp_c": 70},
        "gpu": {"power_limit_w": 25, "clock_limit_mhz": 600, "fan_mode": "max"},
        "fan": {"mode": "max", "speed_percent": None},
        "expected_outcome": "Maximum cooling. Severely reduced performance.",
        "confidence": 1.0, "warnings": ["Emergency thermal mode active"],
    },
}


class ProfileAgent(BaseAgent):
    """
    Profile management agent.
    Stores profiles, learns from sessions, and recommends
    the best profile for the current context.
    """

    def __init__(self, tuner):
        super().__init__()
        self.tuner = tuner
        self.client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        self.model_name = "gemini-1.5-flash-8b"
        os.makedirs(PROFILES_DIR, exist_ok=True)
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        self._init_db()
        self._session_count = 0

    def on_event(self, event: str, data: dict, sender: str):
        if event == "tune_applied":
            settings = data.get("settings", {})
            goal = data.get("goal", "")
            telemetry = self.get_agent("telemetry")
            state = telemetry.get_current_state() if telemetry else {}
            self.log_session(goal, state, settings)
            self._session_count += 1
            self.set_action(f"logged session #{self._session_count}")

    def recommend(self, context: dict) -> str:
        """Ask AI to recommend the best profile for the current context."""
        state = context.get("state", {})
        time_of_day = datetime.now().strftime("%H:%M")
        recent = self._get_recent_sessions(5)

        prompt = f"""You are RyzenSense ProfileAgent.

Based on the current context, recommend ONE profile name from:
gaming, silent, balanced, battery, rendering

Context:
- Time: {time_of_day}
- CPU usage: {state.get('cpu', {}).get('usage_percent', 0)}%
- GPU usage: {state.get('gpu', {}).get('usage_percent', 0)}%
- Battery: {'plugged in' if state.get('battery', {}).get('plugged_in') else str(state.get('battery', {}).get('percent', 100)) + '%'}
- CPU temp: {state.get('cpu', {}).get('temp_c', 0)}°C

Recent sessions: {json.dumps(recent, indent=2)}

Return ONLY the profile name, nothing else."""

        try:
            response = self.client.models.generate_content(
                model=self.model_name, contents=prompt
            )
            recommended = response.text.strip().lower()
            if recommended in BUILTIN_PRESETS:
                self.log(f"Recommended profile: {recommended}")
                return recommended
        except Exception as e:
            self.log(f"Recommendation error: {e}")

        return "balanced"

    def apply_profile(self, name: str) -> bool:
        """Apply a named profile directly."""
        settings = self.load_profile(name)
        if not settings:
            self.log(f"Profile '{name}' not found")
            return False
        self.log(f"Applying profile: {name}")
        self.tuner.apply(settings)
        self.broadcast("tune_applied", {
            "goal": f"profile:{name}",
            "settings": settings,
            "profile": name,
        })
        return True

    def load_profile(self, name: str) -> Optional[dict]:
        if name in BUILTIN_PRESETS:
            return BUILTIN_PRESETS[name]
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        return None

    def save_profile(self, name: str, settings: dict):
        path = os.path.join(PROFILES_DIR, f"{name}.json")
        settings["profile_name"] = name
        settings["saved_at"] = datetime.now().isoformat()
        with open(path, "w") as f:
            json.dump(settings, f, indent=2)
        self.log(f"Saved profile: {name}")

    def list_profiles(self) -> list:
        names = list(BUILTIN_PRESETS.keys())
        for f in os.listdir(PROFILES_DIR):
            if f.endswith(".json") and not f.startswith("_"):
                names.append(f[:-5])
        return names

    def log_session(self, goal: str, state: dict, settings: dict):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    INSERT INTO sessions
                        (timestamp, goal, profile_name, cpu_temp_before,
                         gpu_temp_before, tdp_applied_w, settings_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    datetime.now().isoformat(), goal,
                    settings.get("profile_name", "custom"),
                    state.get("cpu", {}).get("temp_c", 0),
                    state.get("gpu", {}).get("temp_c", 0),
                    settings.get("cpu", {}).get("stapm_limit_w"),
                    json.dumps(settings),
                ))
        except Exception as e:
            self.log(f"Session log error: {e}")

    def log_benchmark(self, data: dict):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    INSERT INTO benchmarks
                        (timestamp, single_thread_s, multi_thread_s,
                         mt_speedup, cpu_temp_before, cpu_temp_after)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    data.get("timestamp"),
                    data.get("single_thread_score", 0),
                    data.get("multi_thread_score", 0),
                    data.get("mt_efficiency", 0),
                    data.get("cpu_temp_before", 0),
                    data.get("cpu_temp_after", 0),
                ))
        except Exception as e:
            self.log(f"Benchmark log error: {e}")

    def show_history(self, limit: int = 20):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute("""
                    SELECT timestamp, goal, profile_name, tdp_applied_w,
                           cpu_temp_before
                    FROM sessions ORDER BY timestamp DESC LIMIT ?
                """, (limit,)).fetchall()
            if not rows:
                print("  No history yet.")
                return
            print(f"\n{'Time':<22} {'Profile':<14} {'TDP':<8} {'CPU°':<7} Goal")
            print("─" * 65)
            for ts, goal, profile, tdp, ct in rows:
                print(f"  {ts[:19]}  {(profile or 'custom'):<14} "
                      f"{str(tdp)+'W':<8} {str(ct)+'°C':<7} {goal[:35]}")
        except Exception as e:
            self.log(f"History error: {e}")

    def _get_recent_sessions(self, limit: int) -> list:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute("""
                    SELECT goal, profile_name, cpu_temp_before
                    FROM sessions ORDER BY timestamp DESC LIMIT ?
                """, (limit,)).fetchall()
            return [{"goal": r[0], "profile": r[1], "cpu_temp": r[2]} for r in rows]
        except Exception:
            return []

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, goal TEXT, profile_name TEXT,
                    cpu_temp_before REAL, gpu_temp_before REAL,
                    tdp_applied_w INTEGER, settings_json TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS benchmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, single_thread_s REAL,
                    multi_thread_s REAL, mt_speedup REAL,
                    cpu_temp_before REAL, cpu_temp_after REAL
                )
            """)