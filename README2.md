# 🔴 RyzenSense v2 — Multi-Agent AMD Hardware Tuner

> **AMD Hackathon Project** — 6 specialized AI agents working together to intelligently tune your AMD hardware.

---

## 🤖 The 6 Agents

| Agent | Icon | Role |
|---|---|---|
| **TelemetryAgent** | 📡 | Continuously polls CPU/GPU/power every second. Broadcasts state to all agents. |
| **TuningAgent** | 🤖 | Uses Gemini AI to convert natural language goals into hardware tuning commands. |
| **WatchdogAgent** | 👁️ | Monitors for thermal emergencies, workload changes, and low battery. Auto-requests tuning. |
| **PredictionAgent** | 🔮 | Forecasts temperatures 30s ahead using linear regression + AI. Acts pre-emptively. |
| **BenchmarkAgent** | ⚡ | Runs CPU benchmarks, compares before/after profiles, tracks performance history. |
| **ProfileAgent** | 📋 | Manages tuning profiles, logs sessions to SQLite, recommends profiles via AI. |

---

## 🔗 How They Communicate

All agents communicate through a central **AgentOrchestrator** message bus:

```
TelemetryAgent  →  broadcasts "telemetry_update" every 1s
                         ↓ (all agents receive this)
WatchdogAgent   →  detects thermal spike → broadcasts "tune_request"
                         ↓
TuningAgent     →  receives request → calls Gemini AI → applies settings
                         ↓
                    broadcasts "tune_applied"
                         ↓
ProfileAgent    →  logs session to SQLite history
BenchmarkAgent  →  optionally runs after-tune benchmark
```

---

## 🚀 Quick Start

```bash
git clone https://github.com/yourname/ryzen-sense-v2
cd ryzen-sense-v2
pip install -r requirements.txt

# Set Gemini API key (free at https://aistudio.google.com/apikey)
set GEMINI_API_KEY=your-key-here     # Windows
export GEMINI_API_KEY=your-key-here  # Linux

# Safe demo (dry-run, no hardware changes)
python demo.py

# Real tuning (needs admin/root for hardware access)
python main.py "gaming mode, stay under 85°C"

# Multi-agent dashboard
python main.py --dashboard

# Background watchdog + prediction (auto-tunes as workload changes)
python main.py --watch
```

---

## 📖 Commands

```bash
python main.py "your goal"          # Natural language tuning
python main.py --dashboard          # Live 6-panel multi-agent UI
python main.py --watch              # Background watchdog mode
python main.py --benchmark          # CPU benchmark
python main.py --agents             # Show all agent statuses
python main.py --profile list       # List profiles
python main.py --profile apply gaming
python main.py --history            # Tuning session history
python main.py --dry-run "gaming"   # Preview without applying
python main.py --reset              # Reset to firmware defaults
```

---

## 🏗 Architecture

```
┌─────────────────────────────────────────────────┐
│              AgentOrchestrator                  │
│         (central message bus / registry)        │
└──────────────────┬──────────────────────────────┘
                   │ broadcasts / receives events
    ┌──────────────┼──────────────────────────┐
    │              │                          │
📡 TelemetryAgent  🤖 TuningAgent        👁️ WatchdogAgent
   (polls hw)       (Gemini AI)           (thermal guard)
    │                    │                     │
    └──── telemetry ─────┘                     │
                         │              tune_request
                    tune_applied              │
                         │              ┌─────┘
                    ┌────┴────┐    🔮 PredictionAgent
                    │         │       (30s forecast)
              📋 ProfileAgent  ⚡ BenchmarkAgent
              (logs + learns)  (perf testing)
```

---

## 📁 File Structure

```
ryzen-sense-v2/
├── main.py                       # CLI entry point
├── demo.py                       # Hackathon demo (safe dry-run)
├── requirements.txt
├── orchestrator/
│   └── orchestrator.py           # Agent message bus
├── agents/
│   ├── base_agent.py             # Base class for all agents
│   ├── telemetry_agent.py        # Hardware polling
│   ├── tuning_agent.py           # Gemini AI tuning
│   ├── watchdog_agent.py         # Thermal + workload monitoring
│   ├── prediction_agent.py       # Proactive forecasting
│   ├── benchmark_agent.py        # Performance testing
│   └── profile_agent.py          # Profile management + learning
├── hardware/
│   ├── telemetry.py              # Low-level hardware readers
│   └── tuner.py                  # ryzenadj / amdsmi applier
├── profiles/
│   └── presets/                  # Built-in JSON profiles
├── ui/
│   └── dashboard.py              # 6-panel rich terminal UI
└── logs/
    └── history.db                # SQLite session history
```

---

## 🏆 Why This Wins

1. **True multi-agent architecture** — agents are independent, communicate via events, and can react to each other
2. **Proactive AI** — doesn't just react to problems, predicts and prevents them
3. **AMD-native** — uses ryzenadj and amdsmi, AMD's own power management APIs
4. **Demo-ready** — natural language interface, live dashboard, impressive agent communication visible in real time
5. **Production quality** — SQLite history, profile learning, safety limits, dry-run mode

---

## ⚙️ Built-in Profiles

| Profile | CPU TDP | Thermal | Fan | Use Case |
|---|---|---|---|---|
| gaming | 65W/78W | 88°C | auto | Max FPS |
| balanced | 45W/54W | 85°C | balanced | Everyday |
| rendering | 95W sustained | 90°C | max | Blender/encode |
| silent | 15W/18W | 75°C | silent | Meetings |
| battery | 10W/12W | 70°C | silent | Battery life |
| emergency_cool | 8W | 70°C | max | Thermal emergency |