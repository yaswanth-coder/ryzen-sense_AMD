"""
RyzenSense AI Agent - Groq Version (llama-3.3-70b-versatile)
"""

import json
import time
import os
from datetime import datetime
from typing import Optional
from groq import Groq

SYSTEM_PROMPT = """You are RyzenSense, an expert AMD hardware tuning AI assistant.

Your job:
1. Read real-time AMD CPU/GPU telemetry data
2. Understand the user's performance goal (gaming, battery, silent, rendering, etc.)
3. Reason about thermal headroom, power budget, and workload requirements
4. Generate precise tuning commands as JSON

## Hardware Context
- If the detected CPU is non-AMD, still generate valid JSON with appropriate TDP values for the system's power envelope
- Thermal control: tctl-temp sets the maximum CPU temperature ceiling
- AMD GPUs (RDNA) support clock speed, power limit, and fan curve adjustments
- Fan modes: auto, max, silent, balanced

## Tuning Rules
- NEVER set tctl-temp above 95C (hardware damage risk)
- NEVER set TDP above the CPU's design limit (check cpu_tdp_limit_w in telemetry)
- If temps are already above 80C, reduce TDP before increasing clocks
- Battery/silent mode: prioritize temps and power over performance
- Gaming/rendering mode: push TDP and clocks to thermal ceiling
- Always leave 5C thermal headroom from the user's stated limit

## Output Format
Return ONLY a valid JSON object with this exact structure:
{
  "reasoning": "2-3 sentence explanation of your decisions",
  "profile_name": "gaming",
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
  "expected_outcome": "what the user should experience after tuning"
}

Return ONLY the JSON. No markdown, no explanation outside the JSON."""


class RyzenSenseAgent:
    def __init__(self, telemetry, tuner, profile_manager):
        self.telemetry = telemetry
        self.tuner = tuner
        self.profile_mgr = profile_manager
        self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        self.model_name = "llama-3.3-70b-versatile"

    def tune(self, user_goal: str) -> dict:
        """Main tuning loop: read state -> reason with AI -> apply settings."""
        print(f"\n🔴 RyzenSense — Goal: \"{user_goal}\"")
        print("─" * 50)

        print("[1/4] Reading hardware telemetry...")
        state = self.telemetry.get_system_state()
        self._print_state_summary(state)

        print("\n[2/4] Consulting AI agent...")
        settings = self._ask_ai(user_goal, state)
        if not settings:
            print("[!] AI failed to generate settings. Aborting.")
            return {}

        print(f"\n      Profile: {settings.get('profile_name', 'custom')}")
        print(f"      Reason:  {settings.get('reasoning', '')}")
        print(f"      Expect:  {settings.get('expected_outcome', '')}")

        print("\n[3/4] Applying hardware settings...")
        self.tuner.apply(settings)

        print("\n[4/4] Saving session to history...")
        self.profile_mgr.log_session(user_goal, state, settings)

        print("\n[✓] Tuning applied. Monitoring for 5s...\n")
        self._monitor_briefly(5)

        return settings

    def _ask_ai(self, goal: str, state: dict) -> Optional[dict]:
        """Call Groq API with telemetry context and user goal."""
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": (
                            f"User goal: {goal}\n\n"
                            f"Current hardware state:\n{json.dumps(state, indent=2)}\n\n"
                            f"Generate optimal AMD tuning settings for this goal. Return ONLY JSON."
                        )
                    }
                ],
                temperature=0.3,
                max_tokens=1024,
            )

            raw = response.choices[0].message.content.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            return json.loads(raw)

        except json.JSONDecodeError as e:
            print(f"[!] AI returned invalid JSON: {e}")
            return None
        except Exception as e:
            print(f"[!] Groq API error: {e}")
            return None

    def chat(self, message: str, history: list) -> str:
        """Interactive chat mode for the dashboard."""
        state = self.telemetry.get_system_state()
        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": f"{message}\n\nCurrent state:\n{json.dumps(state, indent=2)}"
                    }
                ],
                temperature=0.5,
                max_tokens=512,
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error: {e}"

    def run_benchmark(self):
        """Quick CPU benchmark using Python."""
        import math
        import threading

        print("\n🔴 RyzenSense Benchmark")
        print("─" * 40)

        def cpu_stress_single():
            result = 0
            for i in range(1, 2_000_000):
                result += math.sqrt(i) * math.log(i)
            return result

        print("[*] Capturing baseline telemetry...")
        before = self.telemetry.get_system_state()

        print("[*] Running CPU benchmark (single-thread)...")
        start = time.perf_counter()
        cpu_stress_single()
        single_time = time.perf_counter() - start

        print("[*] Running CPU benchmark (multi-thread)...")
        threads = []
        mt_start = time.perf_counter()
        for _ in range(os.cpu_count() or 4):
            t = threading.Thread(target=cpu_stress_single)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        multi_time = time.perf_counter() - mt_start

        after = self.telemetry.get_system_state()

        print("\n── Benchmark Results ──────────────────")
        print(f"  Single-thread:  {single_time:.2f}s")
        print(f"  Multi-thread:   {multi_time:.2f}s")
        print(f"  MT speedup:     {single_time / multi_time:.1f}x")
        print(f"\n── Thermals During Load ───────────────")
        print(f"  CPU temp before: {before['cpu']['temp_c']}°C")
        print(f"  CPU temp after:  {after['cpu']['temp_c']}°C")

        self.profile_mgr.log_benchmark({
            "timestamp": datetime.now().isoformat(),
            "single_thread_s": round(single_time, 3),
            "multi_thread_s": round(multi_time, 3),
            "mt_speedup": round(single_time / multi_time, 2),
            "cpu_temp_before": before["cpu"]["temp_c"],
            "cpu_temp_after": after["cpu"]["temp_c"],
        })

    def _monitor_briefly(self, seconds: int):
        for i in range(seconds):
            state = self.telemetry.get_system_state()
            cpu = state["cpu"]
            gpu = state["gpu"]
            print(
                f"  [{i+1}s] CPU {cpu['freq_mhz']} MHz  "
                f"{cpu['temp_c']}°C  {cpu['usage_percent']}%  |  "
                f"GPU {gpu['usage_percent']}%  {gpu['temp_c']}°C"
            )
            time.sleep(1)

    def _print_state_summary(self, state: dict):
        cpu = state["cpu"]
        gpu = state["gpu"]
        print(f"      CPU: {cpu['model']}")
        print(f"           {cpu['cores']} cores | {cpu['freq_mhz']} MHz | {cpu['temp_c']}°C | {cpu['usage_percent']}% load")
        print(f"      GPU: {gpu['model']}")
        print(f"           {gpu['usage_percent']}% load | {gpu['temp_c']}°C | VRAM {gpu['vram_used_mb']}/{gpu['vram_total_mb']} MB")
        print(f"      PWR: {state['power']['cpu_package_w']}W CPU | {state['power']['gpu_w']}W GPU")
        print(f"      BAT: {'Plugged in' if state['battery']['plugged_in'] else str(state['battery']['percent']) + '% remaining'}") 