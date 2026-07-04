"""
RyzenSense v2 - Telemetry Agent
Continuously polls hardware metrics and broadcasts state updates.
Other agents subscribe to these updates instead of reading hardware directly.
"""

import time
import threading
from collections import deque
from agents.base_agent import BaseAgent


class TelemetryAgent(BaseAgent):
    """
    Dedicated agent for hardware monitoring.
    Broadcasts 'telemetry_update' events every second.
    Maintains rolling history for trend analysis.
    """

    POLL_INTERVAL = 1.0  # seconds
    HISTORY_SIZE = 120   # 2 minutes of history

    def __init__(self, telemetry_engine):
        super().__init__()
        self.engine = telemetry_engine
        self.current_state = None
        self._state_lock = threading.Lock()

        # Rolling history for each metric
        self.history = {
            "cpu_temp": deque(maxlen=self.HISTORY_SIZE),
            "cpu_usage": deque(maxlen=self.HISTORY_SIZE),
            "cpu_freq": deque(maxlen=self.HISTORY_SIZE),
            "gpu_temp": deque(maxlen=self.HISTORY_SIZE),
            "gpu_usage": deque(maxlen=self.HISTORY_SIZE),
            "cpu_power": deque(maxlen=self.HISTORY_SIZE),
            "gpu_power": deque(maxlen=self.HISTORY_SIZE),
        }

    def run(self):
        """Main loop: poll hardware every second and broadcast state."""
        self._running = True
        self.log("Started — polling hardware every 1s")

        while self._running:
            try:
                state = self.engine.get_system_state()

                with self._state_lock:
                    self.current_state = state

                # Update history
                self._update_history(state)

                # Broadcast to all other agents
                self.broadcast("telemetry_update", {
                    "state": state,
                    "trends": self.get_trends(),
                })

                self.set_action(
                    f"CPU {state['cpu']['temp_c']}°C | "
                    f"GPU {state['gpu']['temp_c']}°C"
                )

            except Exception as e:
                self.log(f"Poll error: {e}")

            time.sleep(self.POLL_INTERVAL)

    def get_current_state(self) -> dict:
        """Thread-safe snapshot of the latest state."""
        with self._state_lock:
            return self.current_state or self.engine.get_system_state()

    def get_trends(self) -> dict:
        """Calculate trends from rolling history."""
        def trend(data):
            if len(data) < 5:
                return "stable"
            recent = list(data)[-5:]
            diff = recent[-1] - recent[0]
            if diff > 3:
                return "rising"
            elif diff < -3:
                return "falling"
            return "stable"

        def avg(data):
            return round(sum(data) / len(data), 1) if data else 0

        return {
            "cpu_temp_trend": trend(self.history["cpu_temp"]),
            "gpu_temp_trend": trend(self.history["gpu_temp"]),
            "cpu_usage_trend": trend(self.history["cpu_usage"]),
            "cpu_temp_avg": avg(self.history["cpu_temp"]),
            "gpu_temp_avg": avg(self.history["gpu_temp"]),
            "cpu_power_avg": avg(self.history["cpu_power"]),
        }

    def get_history(self) -> dict:
        """Return full history for dashboard sparklines."""
        return {k: list(v) for k, v in self.history.items()}

    def _update_history(self, state: dict):
        self.history["cpu_temp"].append(state["cpu"]["temp_c"])
        self.history["cpu_usage"].append(state["cpu"]["usage_percent"])
        self.history["cpu_freq"].append(state["cpu"]["freq_mhz"])
        self.history["gpu_temp"].append(state["gpu"]["temp_c"])
        self.history["gpu_usage"].append(state["gpu"]["usage_percent"])
        self.history["cpu_power"].append(state["power"]["cpu_package_w"])
        self.history["gpu_power"].append(state["power"]["gpu_w"])