"""
RyzenSense v2 - Prediction Agent (Groq Version)
"""

import json
import os
import time
from collections import deque
from typing import Optional
from groq import Groq
from agents.base_agent import BaseAgent


class PredictionAgent(BaseAgent):
    CHECK_INTERVAL = 5.0
    FORECAST_HORIZON = 30
    PREEMPTIVE_THRESHOLD = 78
    MIN_HISTORY = 15

    def __init__(self):
        super().__init__()
        self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        self.model_name = "llama-3.3-70b-versatile"
        self._temp_history = {
            "cpu": deque(maxlen=60),
            "gpu": deque(maxlen=60),
        }
        self._last_prediction_time = 0
        self._prediction_cooldown = 45
        self._predictions_made = 0
        self._forecast_log = deque(maxlen=30)

    def on_event(self, event: str, data: dict, sender: str):
        if event != "telemetry_update":
            return

        state = data.get("state", {})
        cpu_temp = state.get("cpu", {}).get("temp_c", 0)
        gpu_temp = state.get("gpu", {}).get("temp_c", 0)

        self._temp_history["cpu"].append(cpu_temp)
        self._temp_history["gpu"].append(gpu_temp)

        now = time.time()
        if (now - self._last_prediction_time) >= self.CHECK_INTERVAL:
            self._run_prediction(state, data.get("trends", {}))
            self._last_prediction_time = now

    def _run_prediction(self, state: dict, trends: dict):
        cpu_history = list(self._temp_history["cpu"])
        gpu_history = list(self._temp_history["gpu"])

        if len(cpu_history) < self.MIN_HISTORY:
            return

        cpu_forecast = self._linear_forecast(cpu_history, self.FORECAST_HORIZON)
        gpu_forecast = self._linear_forecast(gpu_history, self.FORECAST_HORIZON)
        current_cpu = cpu_history[-1]
        current_gpu = gpu_history[-1]

        forecast_entry = {
            "time": time.strftime("%H:%M:%S"),
            "current_cpu": current_cpu,
            "current_gpu": current_gpu,
            "forecast_cpu": round(cpu_forecast, 1),
            "forecast_gpu": round(gpu_forecast, 1),
            "action_taken": False,
        }

        cpu_at_risk = cpu_forecast >= self.PREEMPTIVE_THRESHOLD
        gpu_at_risk = gpu_forecast >= self.PREEMPTIVE_THRESHOLD
        cooldown_ok = (time.time() - self._last_prediction_time) > self._prediction_cooldown

        if (cpu_at_risk or gpu_at_risk) and cooldown_ok:
            self._predictions_made += 1
            self.log(
                f"🔮 Forecast: CPU {current_cpu}°C → {cpu_forecast:.1f}°C | "
                f"GPU {current_gpu}°C → {gpu_forecast:.1f}°C in {self.FORECAST_HORIZON}s"
            )
            goal = self._generate_preemptive_goal(current_cpu, cpu_forecast, current_gpu, gpu_forecast, state, trends)
            if goal:
                self.log(f"🔮 Pre-emptive action: {goal}")
                self.broadcast("tune_request", {
                    "goal": goal,
                    "source": "prediction_agent",
                    "state": state,
                })
                forecast_entry["action_taken"] = True
                self._last_prediction_time = time.time() + self._prediction_cooldown
        else:
            self.set_action(f"forecasting | CPU→{cpu_forecast:.1f}°C GPU→{gpu_forecast:.1f}°C (safe)")

        self._forecast_log.append(forecast_entry)

    def _linear_forecast(self, history: list, horizon_seconds: int) -> float:
        n = len(history)
        if n < 2:
            return history[-1] if history else 0
        x_mean = (n - 1) / 2
        y_mean = sum(history) / n
        numerator = sum((i - x_mean) * (history[i] - y_mean) for i in range(n))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        if denominator == 0:
            return history[-1]
        slope = numerator / denominator
        return max(20.0, min(110.0, history[-1] + slope * horizon_seconds))

    def _generate_preemptive_goal(self, cpu_now, cpu_forecast, gpu_now, gpu_forecast, state, trends) -> Optional[str]:
        prompt = f"""Temperature forecast for the next 30 seconds:
- CPU now: {cpu_now}°C → predicted: {cpu_forecast:.1f}°C
- GPU now: {gpu_now}°C → predicted: {gpu_forecast:.1f}°C

Write ONE short tuning goal (under 20 words) to prevent thermal issues.
Return ONLY the goal string."""

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=50,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip().strip('"')
        except Exception:
            if cpu_forecast > self.PREEMPTIVE_THRESHOLD:
                return f"CPU temperature rising to {cpu_forecast:.0f}°C, reduce power pre-emptively"
            return f"GPU temperature forecast {gpu_forecast:.0f}°C, boost cooling now"

    def get_forecast_log(self) -> list:
        return list(self._forecast_log)

    def get_accuracy(self) -> dict:
        return {
            "predictions_made": self._predictions_made,
            "history_size_cpu": len(self._temp_history["cpu"]),
            "history_size_gpu": len(self._temp_history["gpu"]),
        }