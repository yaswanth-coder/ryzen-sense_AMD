#!/usr/bin/env python3
"""
RyzenSense v2 - Multi-Agent Demo Script
Showcases all 6 agents working together. Safe: dry-run mode.
"""

import time
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def banner(text, char="─"):
    print(f"\n{char * 58}")
    print(f"  {text}")
    print(char * 58)


def main():
    print("\n" + "═" * 58)
    print("  🔴  RyzenSense v2 — Multi-Agent AMD Hardware Tuner")
    print("  AMD Hackathon Demo  |  6 AI Agents")
    print("═" * 58)
    time.sleep(1)

    # Build the multi-agent system
    print("\n[*] Initialising 6-agent system...\n")
    from orchestrator.orchestrator import AgentOrchestrator
    from hardware.telemetry import TelemetryEngine
    from hardware.tuner import HardwareTuner
    from agents.telemetry_agent import TelemetryAgent
    from agents.tuning_agent import TuningAgent
    from agents.watchdog_agent import WatchdogAgent
    from agents.prediction_agent import PredictionAgent
    from agents.benchmark_agent import BenchmarkAgent
    from agents.profile_agent import ProfileAgent

    engine = TelemetryEngine()
    tuner = HardwareTuner(dry_run=True)  # SAFE: no hardware changes

    orc = AgentOrchestrator()
    orc.register("telemetry",  TelemetryAgent(engine))
    orc.register("tuning",     TuningAgent(tuner, None))
    orc.register("watchdog",   WatchdogAgent())
    orc.register("prediction", PredictionAgent())
    orc.register("benchmark",  BenchmarkAgent())
    orc.register("profile",    ProfileAgent(tuner))
    orc.agents["tuning"].profile_mgr = orc.agents["profile"]

    print("  ✓ Orchestrator ready")
    print("  ✓ 6 agents registered:")
    icons = {
        "telemetry": "📡", "tuning": "🤖", "watchdog": "👁️ ",
        "prediction": "🔮", "benchmark": "⚡", "profile": "📋"
    }
    for name in orc.agents:
        print(f"      {icons.get(name,'•')} {name}")

    # Start background agents
    orc.run_all()
    print("\n  [*] All agents started. Collecting telemetry...\n")
    time.sleep(2)

    # ── DEMO 1: Live Telemetry Agent
    banner("DEMO 1: 📡 Telemetry Agent — Live Hardware State")
    telemetry_agent = orc.get_agent("telemetry")
    state = telemetry_agent.get_current_state()
    trends = telemetry_agent.get_trends()
    cpu = state["cpu"]
    gpu = state["gpu"]

    print(f"\n  CPU: {cpu['model']}")
    print(f"       {cpu['cores']} cores | {cpu['freq_mhz']} MHz | {cpu['temp_c']}°C | {cpu['usage_percent']}% load")
    print(f"       Temp trend: {trends.get('cpu_temp_trend', 'stable')} | Avg: {trends.get('cpu_temp_avg', 0)}°C")
    print(f"\n  GPU: {gpu['model']}")
    print(f"       {gpu['usage_percent']}% load | {gpu['temp_c']}°C | {gpu['vram_used_mb']}/{gpu['vram_total_mb']}MB VRAM")
    print(f"\n  PWR: {state['power']['cpu_package_w']}W CPU | {state['power']['gpu_w']}W GPU")
    print(f"  BAT: {'Plugged in' if state['battery']['plugged_in'] else str(state['battery']['percent'])+'%'}")
    time.sleep(2)

    # ── DEMO 2: AI Tuning Agent
    banner("DEMO 2: 🤖 Tuning Agent — Natural Language AI")
    print("\n  [DRY RUN — no hardware changes]\n")
    print("  Goal: \"gaming mode, max fps, stay under 85°C\"\n")
    result = orc.agents["tuning"].tune("gaming mode, max fps, stay under 85°C")
    time.sleep(2)

    # ── DEMO 3: Watchdog Agent
    banner("DEMO 3: 👁️  Watchdog Agent — Auto Thermal Protection")
    watchdog = orc.get_agent("watchdog")
    print(f"\n  Watchdog is monitoring:")
    print(f"  • CPU critical threshold: 90°C → emergency cool")
    print(f"  • CPU warning threshold:  80°C → request tune")
    print(f"  • GPU critical threshold: 92°C → emergency cool")
    print(f"  • Workload detection: gaming / rendering / idle")
    print(f"  • Battery protection: auto power-save at 15%")
    alerts = watchdog.get_alerts()
    if alerts:
        print(f"\n  Recent alerts:")
        for a in alerts[-3:]:
            print(f"    [{a['time']}] [{a['level']}] {a['message']}")
    else:
        print(f"\n  ✓ No thermal alerts — system is healthy")
    time.sleep(2)

    # ── DEMO 4: Prediction Agent
    banner("DEMO 4: 🔮 Prediction Agent — Proactive Forecasting")
    prediction = orc.get_agent("prediction")
    forecasts = prediction.get_forecast_log()
    acc = prediction.get_accuracy()
    print(f"\n  Predictions made: {acc['predictions_made']}")
    print(f"  Data collected: {acc['history_size_cpu']} CPU samples | {acc['history_size_gpu']} GPU samples")
    if forecasts:
        latest = forecasts[-1]
        print(f"\n  Latest forecast (30s ahead):")
        print(f"    CPU: {latest['current_cpu']}°C → {latest['forecast_cpu']}°C")
        print(f"    GPU: {latest['current_gpu']}°C → {latest['forecast_gpu']}°C")
        print(f"    Action taken: {'Yes ⚡' if latest['action_taken'] else 'No (safe)'}")
    else:
        print(f"\n  Forecasting engine active — collecting baseline data...")
        print(f"  (Needs ~15 seconds of data to start predicting)")
    time.sleep(2)

    # ── DEMO 5: Benchmark Agent
    banner("DEMO 5: ⚡ Benchmark Agent — Performance Measurement")
    print("\n  Running CPU benchmark (all threads)...\n")
    results = orc.agents["benchmark"].run_benchmark(label="demo-baseline")
    time.sleep(2)

    # ── DEMO 6: Profile Agent
    banner("DEMO 6: 📋 Profile Agent — Smart Profile Management")
    profile_agent = orc.get_agent("profile")
    profiles = profile_agent.list_profiles()
    print(f"\n  Available profiles: {', '.join(profiles)}")

    print(f"\n  Asking AI to recommend best profile for current context...")
    state = telemetry_agent.get_current_state()
    recommended = profile_agent.recommend({"state": state})
    print(f"  🤖 AI Recommendation: '{recommended}'")
    print(f"\n  Session history logged: {profile_agent._session_count} sessions this run")
    time.sleep(2)

    # ── DEMO 7: Agent Communication
    banner("DEMO 7: 🔗 Multi-Agent Communication — Event Bus")
    print(f"\n  Recent inter-agent events:")
    events = orc.get_event_log(10)
    shown = 0
    for e in reversed(events):
        if e["event"] == "telemetry_update":
            continue
        print(f"    [{e['timestamp'][11:19]}] {e['sender']:12} → {e['event']}")
        shown += 1
        if shown >= 6:
            break

    # ── Summary
    print("\n" + "═" * 58)
    print("  ✓  RyzenSense v2 Multi-Agent Demo Complete!\n")
    print("  Agents demonstrated:")
    print("    📡 TelemetryAgent  — real-time hardware polling")
    print("    🤖 TuningAgent     — Gemini AI natural language tuning")
    print("    👁️  WatchdogAgent  — thermal emergency detection")
    print("    🔮 PredictionAgent — proactive 30s thermal forecast")
    print("    ⚡ BenchmarkAgent  — CPU performance measurement")
    print("    📋 ProfileAgent    — AI profile recommendation")
    print()
    print("  Stack: Python · Gemini AI · ryzenadj · amdsmi · rich")
    print("═" * 58 + "\n")

    orc.stop_all()


if __name__ == "__main__":
    main()