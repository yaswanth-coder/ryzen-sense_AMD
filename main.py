#!/usr/bin/env python3
"""
RyzenSense v2 - Multi-Agent AMD Hardware Performance Tuner
AMD Hackathon Project

6 Specialized AI Agents working together:
  📡 TelemetryAgent   — continuous hardware monitoring
  🤖 TuningAgent      — AI-powered natural language tuning
  👁️  WatchdogAgent   — thermal emergency detection & auto-tune
  🔮 PredictionAgent  — proactive thermal forecasting
  ⚡ BenchmarkAgent   — performance measurement & comparison
  📋 ProfileAgent     — profile management & session learning

Usage:
  python main.py "gaming mode, stay under 85C"
  python main.py --dashboard
  python main.py --benchmark
  python main.py --profile list
  python main.py --watch          (watchdog + prediction only, no goal)
  python main.py --agents         (show all agent statuses)
"""

import argparse
import sys
import os
import time


def build_system(dry_run: bool = False):
    """Build and wire up all agents via the orchestrator."""
    from orchestrator.orchestrator import AgentOrchestrator
    from hardware.telemetry import TelemetryEngine
    from hardware.tuner import HardwareTuner
    from agents.telemetry_agent import TelemetryAgent
    from agents.tuning_agent import TuningAgent
    from agents.watchdog_agent import WatchdogAgent
    from agents.prediction_agent import PredictionAgent
    from agents.benchmark_agent import BenchmarkAgent
    from agents.profile_agent import ProfileAgent

    # Hardware backends
    engine = TelemetryEngine()
    tuner = HardwareTuner(dry_run=dry_run)

    # Create orchestrator
    orc = AgentOrchestrator()

    # Create and register all agents
    orc.register("telemetry",  TelemetryAgent(engine))
    orc.register("tuning",     TuningAgent(tuner, None))       # profile_mgr set below
    orc.register("watchdog",   WatchdogAgent())
    orc.register("prediction", PredictionAgent())
    orc.register("benchmark",  BenchmarkAgent())
    orc.register("profile",    ProfileAgent(tuner))

    # Give tuning agent a reference to profile agent as its profile manager
    orc.agents["tuning"].profile_mgr = orc.agents["profile"]

    return orc


def main():
    parser = argparse.ArgumentParser(
        description="RyzenSense v2 — Multi-Agent AMD Hardware Tuner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py "gaming mode, max fps"
  python main.py "battery saver, 2 hours remaining"
  python main.py --dashboard
  python main.py --benchmark
  python main.py --watch
  python main.py --profile list
  python main.py --profile apply gaming
  python main.py --agents
  python main.py --dry-run "rendering mode"
        """
    )

    parser.add_argument("goal", nargs="?", help="Natural language tuning goal")
    parser.add_argument("--dashboard", action="store_true", help="Multi-agent live dashboard")
    parser.add_argument("--benchmark", action="store_true", help="Run performance benchmark")
    parser.add_argument("--watch", action="store_true", help="Run watchdog + prediction in background")
    parser.add_argument("--agents", action="store_true", help="Show all agent statuses")
    parser.add_argument("--profile", nargs="+", metavar=("ACTION", "NAME"),
                        help="Profile: list | apply <name> | save <name>")
    parser.add_argument("--history", action="store_true", help="Show tuning history")
    parser.add_argument("--dry-run", action="store_true", help="Preview without applying")
    parser.add_argument("--reset", action="store_true", help="Reset to firmware defaults")
    args = parser.parse_args()

    print("\n🔴 RyzenSense v2 — Multi-Agent AMD AI Hardware Tuner")
    print("=" * 55)

    if args.dry_run:
        print("  [DRY RUN] No hardware changes will be applied.\n")

    # Build the system
    orc = build_system(dry_run=args.dry_run)

    # ── Dashboard: start all agents, show live UI
    if args.dashboard:
        from ui.dashboard import MultiAgentDashboard
        print("  Starting all 6 agents...")
        orc.run_all()
        time.sleep(1)  # Let telemetry agent collect first sample
        dashboard = MultiAgentDashboard(orc)
        dashboard.run()
        orc.stop_all()
        return

    # ── Watch mode: background watchdog + prediction
    if args.watch:
        print("  Starting watchdog + prediction agents...")
        print("  They will auto-tune when thermals spike or workload changes.")
        print("  Press Ctrl+C to stop.\n")
        orc.run_all()
        try:
            while True:
                status = orc.status()
                for name, s in status.items():
                    print(f"  [{name}] {s['last_action']}")
                print()
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n  Stopping agents...")
            orc.stop_all()
        return

    # ── Agent status
    if args.agents:
        orc.run_all()
        time.sleep(1)
        print("\n── Agent Status ────────────────────────────────")
        status = orc.status()
        icons = {
            "telemetry": "📡", "tuning": "🤖", "watchdog": "👁️",
            "prediction": "🔮", "benchmark": "⚡", "profile": "📋"
        }
        for name, s in status.items():
            icon = icons.get(name, "•")
            print(f"  {icon}  {name:<12} {s['last_action']}")
        orc.stop_all()
        return

    # ── Profile management
    if args.profile:
        action = args.profile[0]
        name = args.profile[1] if len(args.profile) > 1 else None
        profile_agent = orc.agents["profile"]

        if action == "list":
            profiles = profile_agent.list_profiles()
            print("\n── Available Profiles ────────────────────────")
            for p in profiles:
                tag = "(built-in)" if p in profile_agent.load_profile(p) else "(custom)"
                print(f"  • {p}")
        elif action == "apply" and name:
            orc.run_all()
            time.sleep(0.5)
            profile_agent.apply_profile(name)
            orc.stop_all()
        elif action == "save" and name:
            telemetry = orc.agents["telemetry"]
            state = telemetry.engine.get_system_state()
            profile_agent.save_profile(name, {"snapshot": state})
        return

    # ── History
    if args.history:
        orc.agents["profile"].show_history()
        return

    # ── Reset
    if args.reset:
        orc.agents["tuning"].tuner.reset_to_defaults()
        return

    # ── Benchmark
    if args.benchmark:
        orc.run_all()
        time.sleep(1)
        print("\n  Running benchmark with all agents active...\n")
        orc.agents["benchmark"].set_baseline()
        orc.stop_all()
        return

    # ── Natural language tuning (main use case)
    if args.goal:
        # Start telemetry and profile agents
        orc.run_all()
        time.sleep(1)  # Let telemetry collect first reading

        print(f"\n  Goal: \"{args.goal}\"")
        print("  Starting all agents...\n")

        result = orc.agents["tuning"].tune(args.goal)

        if result:
            print(f"\n  ✓ Tuning complete!")
            print(f"  Profile: {result.get('profile_name')}")
            print(f"  Expected: {result.get('expected_outcome')}")

        orc.stop_all()
        return

    # ── No args: show system snapshot
    telemetry = orc.agents["telemetry"]
    state = telemetry.engine.get_system_state()
    cpu = state["cpu"]
    gpu = state["gpu"]
    print(f"\n  CPU: {cpu['model']}")
    print(f"       {cpu['cores']}C/{cpu['threads']}T | {cpu['freq_mhz']} MHz | {cpu['temp_c']}°C | {cpu['usage_percent']}% load")
    print(f"  GPU: {gpu['model']}")
    print(f"       {gpu['usage_percent']}% load | {gpu['temp_c']}°C")
    print(f"\n  6 agents ready. Try: python main.py \"gaming mode\"")
    print(f"  Or: python main.py --dashboard")


if __name__ == "__main__":
    main()