"""
RyzenSense v2 - Tuning Agent (Groq Version)
Uses Groq API (free) with Llama 3.3 70B for fast AI tuning.
"""

import json
import os
from typing import Optional
from groq import Groq
from agents.base_agent import BaseAgent


SYSTEM_PROMPT = """You are RyzenSense TuningAgent, an expert AMD hardware optimizer.

You receive real-time telemetry + trend data + a user goal, and return precise JSON tuning commands.

## Rules
- NEVER set tctl_temp_c above 95 (safety limit)
- NEVER exceed cpu_tdp_limit_w from telemetry
- If cpu_temp_trend is 'rising', reduce TDP by 10% before boosting clocks
- If gpu_temp > 85, set fan_mode to 'max' regardless of goal
- Battery/silent: minimize power. Gaming/rendering: maximize within thermal budget.
- Always leave 5C headroom from user's stated thermal limit

## Output — return ONLY this JSON, nothing else:
{
  "reasoning": "2-3 sentences explaining trade-offs",
  "profile_name": "gaming|silent|balanced|rendering|battery|custom",
  "confidence": 0.95,
  "cpu": {
    "stapm_limit_w": 45,
    "fast_limit_w": 54,
    "slow_limit_w": 45,
    "tctl_temp_c": 85
  },
  "gpu": {
    "power_limit_w": 120,
    "clock_limit_mhz": null,
    "fan_mode": "auto"
  },
  "fan": {
    "mode": "auto",
    "speed_percent": null
  },
  "expected_outcome": "one sentence user-facing result",
  "warnings": []
}"""


class TuningAgent(BaseAgent):
    def __init__(self, tuner, profile_manager):
        super().__init__()
        self.tuner = tuner
        self.profile_mgr = profile_manager
        self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        self.model_name = "llama-3.3-70b-versatile"
        self.last_settings = None
        self.tune_count = 0

    def on_event(self, event: str, data: dict, sender: str):
        if event == "tune_request":
            goal = data.get("goal", "balanced")
            source = data.get("source", sender)
            self.log(f"Tune request from {source}: '{goal}'")
            self.tune(goal, state=data.get("state"))
        elif event == "emergency_cool":
            self.log(f"⚠️  Emergency cooling triggered by {sender}!")
            self.tune("emergency thermal protection - reduce power immediately")

    def tune(self, user_goal: str, state: dict = None) -> dict:
        self.set_action(f"tuning: {user_goal[:30]}")
        self.tune_count += 1

        if state is None:
            telemetry_agent = self.get_agent("telemetry")
            state = telemetry_agent.get_current_state() if telemetry_agent else {}

        trends = {}
        telemetry_agent = self.get_agent("telemetry")
        if telemetry_agent:
            trends = telemetry_agent.get_trends()

        self.log(f"Generating AI tuning plan for: '{user_goal}'")
        settings = self._ask_ai(user_goal, state, trends)

        if not settings:
            self.log("AI failed to generate settings")
            return {}

        for warning in settings.get("warnings", []):
            self.log(f"⚠️  Warning: {warning}")

        self.log(
            f"Plan: {settings.get('profile_name')} | "
            f"Confidence: {settings.get('confidence', 0)*100:.0f}% | "
            f"CPU {settings['cpu']['stapm_limit_w']}W / {settings['cpu']['tctl_temp_c']}°C"
        )

        self.tuner.apply(settings)
        self.last_settings = settings

        if self.profile_mgr:
            self.profile_mgr.log_session(user_goal, state, settings)

        self.broadcast("tune_applied", {
            "goal": user_goal,
            "settings": settings,
            "profile": settings.get("profile_name"),
        })

        self.set_action(f"applied: {settings.get('profile_name')}")
        return settings

    def _ask_ai(self, goal: str, state: dict, trends: dict) -> Optional[dict]:
        user_message = f"""User goal: {goal}

Hardware trends (last 2 minutes):
{json.dumps(trends, indent=2)}

Current hardware state:
{json.dumps(state, indent=2)}

Generate optimal AMD tuning settings. Return ONLY JSON."""

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=1024,
                temperature=0.3,
            )
            raw = response.choices[0].message.content.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw.strip())

        except json.JSONDecodeError as e:
            self.log(f"Invalid JSON from AI: {e}")
            return None
        except Exception as e:
            self.log(f"Groq API error: {e}")
            return None