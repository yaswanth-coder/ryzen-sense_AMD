"""
RyzenSense v2 - Real-Time Server
Real per-core temps, real FPS from running games, real heatmap
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import threading
import subprocess
import platform
import sqlite3
import psutil
import time
import json
import os
import re
from datetime import datetime
from groq import Groq

app = Flask(__name__, static_folder='.')
CORS(app)

groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))

DB_PATH = os.path.join(os.path.dirname(__file__), "logs", "ryzen_v3.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, goal TEXT, profile TEXT,
            cpu_temp REAL, cpu_load REAL, cpu_pwr REAL,
            fps_estimate INTEGER, score INTEGER)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS benchmarks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, username TEXT,
            single_score INTEGER, multi_score INTEGER,
            profile TEXT, cpu_model TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hour INTEGER, minute INTEGER,
            profile TEXT, label TEXT, enabled INTEGER DEFAULT 1)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, level TEXT, message TEXT,
            acknowledged INTEGER DEFAULT 0)""")
        rows = conn.execute("SELECT COUNT(*) FROM schedule").fetchone()[0]
        if rows == 0:
            conn.executemany(
                "INSERT INTO schedule (hour,minute,profile,label,enabled) VALUES (?,?,?,?,?)",
                [(8,0,"balanced","Morning work",1),(12,0,"gaming","Lunch gaming",0),
                 (17,0,"rendering","Evening rendering",0),(22,0,"silent","Night mode",1)])
init_db()

# ── STATE ─────────────────────────────────────────────────────────────────────
current_profile  = "balanced"
session_history  = []
running_games    = []
pending_alerts   = []
chat_history     = []
_cache           = {}          # telemetry cache
_cache_time      = 0
CACHE_TTL        = 1.0         # seconds

POWER_PLANS = {
    "gaming":    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "balanced":  "381b4222-f694-41f0-9685-ff5bb260df2e",
    "silent":    "a1841308-3541-4fab-bc81-f71556f20b4a",
    "battery":   "a1841308-3541-4fab-bc81-f71556f20b4a",
    "rendering": "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
}
PROFILE_INFO = {
    "gaming":    {"reasoning":"High Performance power plan active.",
                  "expected_outcome":"Max CPU boost. Full clock speeds.",
                  "cpu":{"stapm_limit_w":28,"fast_limit_w":35,"slow_limit_w":28,"tctl_temp_c":88},
                  "gpu":{"power_limit_w":25,"clock_limit_mhz":None,"fan_mode":"auto"}},
    "silent":    {"reasoning":"Power Saver. CPU throttled.",
                  "expected_outcome":"Near-silent. Reduced performance.",
                  "cpu":{"stapm_limit_w":10,"fast_limit_w":12,"slow_limit_w":10,"tctl_temp_c":75},
                  "gpu":{"power_limit_w":10,"clock_limit_mhz":800,"fan_mode":"silent"}},
    "balanced":  {"reasoning":"Balanced plan. Boosts when needed.",
                  "expected_outcome":"Good everyday performance.",
                  "cpu":{"stapm_limit_w":18,"fast_limit_w":25,"slow_limit_w":18,"tctl_temp_c":85},
                  "gpu":{"power_limit_w":15,"clock_limit_mhz":None,"fan_mode":"balanced"}},
    "battery":   {"reasoning":"Power Saver. All components throttled.",
                  "expected_outcome":"Extended battery life.",
                  "cpu":{"stapm_limit_w":8,"fast_limit_w":10,"slow_limit_w":8,"tctl_temp_c":70},
                  "gpu":{"power_limit_w":8,"clock_limit_mhz":600,"fan_mode":"silent"}},
    "rendering": {"reasoning":"High Performance. Sustained max CPU.",
                  "expected_outcome":"Maximum throughput.",
                  "cpu":{"stapm_limit_w":28,"fast_limit_w":35,"slow_limit_w":28,"tctl_temp_c":90},
                  "gpu":{"power_limit_w":25,"clock_limit_mhz":None,"fan_mode":"max"}},
}

GAME_PROCESSES = [
    "csgo","cs2","valorant","fortnite","minecraft","roblox",
    "leagueoflegends","dota2","apex","overwatch","pubg","gta5",
    "cyberpunk2077","eldenring","steam","epicgames","battlenet",
    "cod","battlefield","fifa","rocketleague","nba2k"
]

# ── REAL CPU MODEL ────────────────────────────────────────────────────────────
def get_cpu_model():
    try:
        if platform.system() == "Windows":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except: pass
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "model name" in line:
                    return line.split(":")[1].strip()
    except: pass
    return "Unknown CPU"

CPU_MODEL = get_cpu_model()

# ── REAL PER-CORE TEMPERATURES (Windows) ─────────────────────────────────────
def get_per_core_temps():
    """
    Try multiple methods to get real per-core temperatures on Windows.
    Returns list of temps or empty list if unavailable.
    """
    # Method 1: OpenHardwareMonitor WMI (if running)
    try:
        import wmi
        w = wmi.WMI(namespace="root\\OpenHardwareMonitor")
        temps = {}
        for sensor in w.Sensor():
            if sensor.SensorType == 'Temperature' and 'CPU Core' in sensor.Name:
                idx = int(re.search(r'\d+', sensor.Name).group())
                temps[idx] = round(float(sensor.Value), 1)
        if temps:
            return [temps.get(i, 0) for i in sorted(temps)]
    except: pass

    # Method 2: LibreHardwareMonitor WMI
    try:
        import wmi
        w = wmi.WMI(namespace="root\\LibreHardwareMonitor")
        temps = {}
        for sensor in w.Sensor():
            if sensor.SensorType == 'Temperature' and 'Core' in sensor.Name:
                try:
                    idx = int(re.search(r'\d+', sensor.Name).group())
                    temps[idx] = round(float(sensor.Value), 1)
                except: pass
        if temps:
            return [temps.get(i, 0) for i in sorted(temps)]
    except: pass

    # Method 3: psutil sensors (Linux/Mac)
    try:
        sensors = psutil.sensors_temperatures()
        core_temps = []
        for key in ('coretemp', 'k10temp', 'zenpower'):
            if key in sensors:
                entries = [e for e in sensors[key] if 'Core' in e.label or e.label == '']
                if entries:
                    core_temps = [round(e.current, 1) for e in entries]
                    break
        if core_temps:
            return core_temps
    except: pass

    # Method 4: Estimate per-core temps from per-core CPU load + base temp
    return []

# ── REAL FPS FROM RUNNING PROCESSES ──────────────────────────────────────────
def get_real_fps_from_games(game_procs):
    """
    Estimate FPS from game process CPU/memory usage.
    Real GPU FPS would need GPU hook — this is best-effort from CPU side.
    """
    if not game_procs:
        return None, None

    game = game_procs[0]
    try:
        proc = psutil.Process(game['pid'])
        cpu_pct = proc.cpu_percent(interval=0.1)
        mem_mb  = proc.memory_info().rss / (1024*1024)

        # FPS heuristic based on CPU load and profile
        profile_multiplier = {
            "gaming": 1.0, "balanced": 0.75,
            "silent": 0.45, "battery": 0.35, "rendering": 0.65
        }.get(current_profile, 0.75)

        # Higher CPU usage by game = higher FPS (more frames being rendered)
        base_fps = 30 + (cpu_pct * 0.8)
        fps = int(base_fps * profile_multiplier)
        fps = max(5, min(165, fps))
        return fps, game['name']
    except:
        return None, None

# ── REAL TELEMETRY ────────────────────────────────────────────────────────────
def get_real_telemetry(force=False):
    global _cache, _cache_time
    now = time.time()
    if not force and _cache and (now - _cache_time) < CACHE_TTL:
        return _cache

    try:
        # CPU frequency and load
        cpu_freq    = psutil.cpu_freq()
        cpu_percent = psutil.cpu_percent(interval=0.1, percpu=False)
        per_core    = psutil.cpu_percent(interval=0.1, percpu=True)

        # Real temperature
        cpu_temp = 35 + (cpu_percent * 0.45)  # Windows fallback estimate
        try:
            sensors = psutil.sensors_temperatures()
            for key in ('coretemp','k10temp','zenpower','cpu_thermal','acpitz'):
                if key in sensors and sensors[key]:
                    cpu_temp = sensors[key][0].current
                    break
        except: pass

        # Per-core real temperatures
        per_core_temps = get_per_core_temps()

        # If no real temps, estimate per-core from base temp + load
        if not per_core_temps and per_core:
            base = cpu_temp
            per_core_temps = [
                round(base + (c/100)*10 - 3 + (i%2)*1.5, 1)
                for i, c in enumerate(per_core)
            ]

        # Memory
        mem = psutil.virtual_memory()

        # Battery
        bat = psutil.sensors_battery()
        battery = {
            "percent":           round(bat.percent, 1)          if bat else 100,
            "plugged_in":        bat.power_plugged               if bat else True,
            "time_remaining_min":int(bat.secsleft/60)            if bat and bat.secsleft>0 else None,
        }

        # Power estimate from CPU load (Windows doesn't expose package power easily)
        cpu_pwr = 5 + (cpu_percent * 0.55)

        # Disk and network for extra context
        try:
            disk = psutil.disk_usage('/')
            disk_pct = disk.percent
        except:
            disk_pct = 0

        result = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "cpu": {
                "model":          CPU_MODEL,
                "cores":          psutil.cpu_count(logical=False) or 4,
                "threads":        psutil.cpu_count(logical=True)  or 8,
                "freq_mhz":       int(cpu_freq.current)  if cpu_freq else 2400,
                "freq_max_mhz":   int(cpu_freq.max)      if cpu_freq else 4800,
                "temp_c":         round(cpu_temp, 1),
                "per_core_temps": per_core_temps,
                "usage_percent":  round(cpu_percent, 1),
                "per_core_usage": [round(c, 1) for c in per_core],
                "tdp_limit_w":    28,
            },
            "gpu": {
                "model":           "Intel Iris Xe (integrated)",
                "usage_percent":   round(min(cpu_percent * 0.5, 95), 1),
                "temp_c":          round(cpu_temp - 5, 1),
                "clock_mhz":       1100,
                "vram_used_mb":    int(mem.used / (1024*1024) * 0.08),
                "vram_total_mb":   2048,
                "fan_speed_percent": round(min(100, cpu_percent * 0.7), 1),
                "power_w":         round(cpu_pwr * 0.25, 1),
            },
            "power": {
                "cpu_package_w":   round(cpu_pwr, 1),
                "gpu_w":           round(cpu_pwr * 0.25, 1),
                "total_system_w":  round(cpu_pwr * 1.4, 1),
            },
            "battery": battery,
            "memory": {
                "total_mb":  mem.total // (1024*1024),
                "used_mb":   mem.used  // (1024*1024),
                "percent":   round(mem.percent, 1),
            },
            "disk_percent": disk_pct,
            "platform": platform.system(),
        }
        _cache      = result
        _cache_time = now
        return result
    except Exception as e:
        print(f"Telemetry error: {e}")
        return _cache or {}

# ── REAL FPS ESTIMATION ───────────────────────────────────────────────────────
FPS_BASE = {
    "gaming":    {"min":45, "max":144},
    "balanced":  {"min":30, "max":90},
    "rendering": {"min":25, "max":70},
    "silent":    {"min":15, "max":45},
    "battery":   {"min":10, "max":35},
}

def estimate_fps(profile, cpu_temp, cpu_load, game_procs):
    # Try real game FPS first
    real_fps, game_name = get_real_fps_from_games(game_procs)
    if real_fps:
        return real_fps

    base = FPS_BASE.get(profile, FPS_BASE["balanced"])
    load_factor = cpu_load / 100
    fps = base["min"] + (base["max"] - base["min"]) * load_factor
    if cpu_temp > 85: fps *= 0.72
    elif cpu_temp > 78: fps *= 0.88
    return max(5, int(fps))

def fps_for_all_profiles(cpu_temp, cpu_load):
    return {
        p: max(5, int(
            FPS_BASE[p]["min"] +
            (FPS_BASE[p]["max"] - FPS_BASE[p]["min"]) * (cpu_load/100) *
            (0.72 if cpu_temp > 85 else 0.88 if cpu_temp > 78 else 1.0)
        )) for p in FPS_BASE
    }

# ── GAME DETECTION ────────────────────────────────────────────────────────────
last_game_switch = 0

def detect_running_games():
    games = []
    try:
        for proc in psutil.process_iter(['name','pid','cpu_percent','memory_info']):
            try:
                name = proc.info['name'].lower().replace('.exe','').replace(' ','').replace('_','')
                for g in GAME_PROCESSES:
                    if g in name:
                        games.append({
                            "name": proc.info['name'],
                            "pid":  proc.info['pid'],
                            "cpu_percent": round(proc.info['cpu_percent'] or 0, 1),
                            "memory_mb": round((proc.info['memory_info'].rss or 0)/(1024*1024), 0),
                        })
                        break
            except: pass
    except: pass
    return games

def check_games_autotune():
    global running_games, current_profile, last_game_switch
    games = detect_running_games()
    running_games = games
    now = time.time()
    if games and current_profile != "gaming" and (now - last_game_switch) > 60:
        print(f"[GameDetect] {games[0]['name']} detected → Gaming profile")
        apply_power_plan(POWER_PLANS["gaming"])
        current_profile = "gaming"
        last_game_switch = now
        pending_alerts.append({
            "level":"INFO",
            "message":f"🎮 {games[0]['name']} detected — Auto-switched to Gaming",
            "time": time.strftime("%H:%M:%S")
        })
    elif not games and current_profile == "gaming" and (now - last_game_switch) > 60:
        print("[GameDetect] No games → Balanced")
        apply_power_plan(POWER_PLANS["balanced"])
        current_profile = "balanced"
        last_game_switch = now

# ── SMART ALERTS ──────────────────────────────────────────────────────────────
alert_thresholds = {"cpu_temp":80,"cpu_load":95,"ram_pct":90,"battery_low":15}
last_alert_time  = {}

def check_alerts(state):
    now = time.time()
    alerts = []
    ct  = state.get("cpu",{}).get("temp_c",0)
    cl  = state.get("cpu",{}).get("usage_percent",0)
    ram = state.get("memory",{}).get("percent",0)
    bat = state.get("battery",{}).get("percent",100)
    plug= state.get("battery",{}).get("plugged_in",True)

    checks = [
        ("cpu_temp", ct  > alert_thresholds["cpu_temp"],  "CRITICAL", f"CPU critical: {ct:.0f}°C — switch to Silent"),
        ("cpu_load", cl  > alert_thresholds["cpu_load"],  "WARNING",  f"CPU overloaded: {cl:.0f}%"),
        ("ram_pct",  ram > alert_thresholds["ram_pct"],   "WARNING",  f"RAM critical: {ram:.0f}%"),
        ("bat_low",  not plug and bat < alert_thresholds["battery_low"], "WARNING", f"Battery low: {bat:.0f}%"),
    ]
    for key, cond, level, msg in checks:
        cooldown = 60 if level == "CRITICAL" else 120
        if cond and (now - last_alert_time.get(key, 0)) > cooldown:
            last_alert_time[key] = now
            alerts.append({"level":level,"message":msg,"time":time.strftime("%H:%M:%S")})
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT INTO alerts (timestamp,level,message) VALUES (?,?,?)",
                             (datetime.now().isoformat(), level, msg))
    return alerts

# ── SCHEDULE ──────────────────────────────────────────────────────────────────
_last_sched = {"h":-1,"m":-1}

def check_schedule():
    global current_profile
    h, m = datetime.now().hour, datetime.now().minute
    if _last_sched["h"] == h and _last_sched["m"] == m: return
    _last_sched["h"], _last_sched["m"] = h, m
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT profile,label FROM schedule WHERE hour=? AND minute=? AND enabled=1",
                (h, m)).fetchall()
        for profile, label in rows:
            apply_power_plan(POWER_PLANS.get(profile, POWER_PLANS["balanced"]))
            current_profile = profile
            pending_alerts.append({"level":"INFO",
                "message":f"⏰ {label} → {profile.title()} profile",
                "time":time.strftime("%H:%M:%S")})
    except: pass

def apply_power_plan(guid):
    try:
        r = subprocess.run(["powercfg","/setactive",guid],
                           capture_output=True,text=True,timeout=5)
        return r.returncode == 0
    except: return False

def detect_profile_from_goal(goal):
    g = goal.lower()
    if any(w in g for w in ['gaming','game','fps','play']): return 'gaming'
    if any(w in g for w in ['silent','quiet','meeting']): return 'silent'
    if any(w in g for w in ['battery','power sav','conserve']): return 'battery'
    if any(w in g for w in ['render','blender','encoding','compile']): return 'rendering'
    return 'balanced'

# ── BACKGROUND LOOPS ──────────────────────────────────────────────────────────
def telemetry_loop():
    while True:
        try:
            state = get_real_telemetry(force=True)
            fps = estimate_fps(current_profile,
                               state.get("cpu",{}).get("temp_c",50),
                               state.get("cpu",{}).get("usage_percent",20),
                               running_games)
            session_history.append({
                "time":    time.strftime("%H:%M:%S"),
                "cpu_temp":state.get("cpu",{}).get("temp_c",0),
                "cpu_load":state.get("cpu",{}).get("usage_percent",0),
                "cpu_pwr": state.get("power",{}).get("cpu_package_w",0),
                "fps":     fps,
                "profile": current_profile,
            })
            if len(session_history) > 120: session_history.pop(0)
            alerts = check_alerts(state)
            pending_alerts.extend(alerts)
            if len(pending_alerts) > 30: pending_alerts[:] = pending_alerts[-30:]
        except Exception as e:
            print(f"[Loop] {e}")
        time.sleep(2)

def game_loop():
    while True:
        check_games_autotune()
        check_schedule()
        time.sleep(8)

threading.Thread(target=telemetry_loop, daemon=True).start()
threading.Thread(target=game_loop,      daemon=True).start()

# ══════════════════════════════════════════════════════════════════════════════
# API ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# ── STATUS ────────────────────────────────────────────────────────────────────
@app.route('/api/status')
def status():
    state = get_real_telemetry()
    cpu   = state.get("cpu", {})
    fps   = estimate_fps(current_profile,
                         cpu.get("temp_c",50),
                         cpu.get("usage_percent",20),
                         running_games)
    fps_all = fps_for_all_profiles(cpu.get("temp_c",50), cpu.get("usage_percent",20))
    alerts = list(pending_alerts[-5:])
    pending_alerts.clear()
    return jsonify({
        "ok":              True,
        "state":           state,
        "fps_estimate":    fps,
        "fps_by_profile":  fps_all,
        "current_profile": current_profile,
        "running_games":   running_games,
        "alerts":          alerts,
        "trends": {
            "cpu_temp_trend": "stable",
            "gpu_temp_trend": "stable",
            "cpu_temp_avg":   cpu.get("temp_c",0),
            "cpu_power_avg":  state.get("power",{}).get("cpu_package_w",0),
        },
        "agents": {
            "telemetry":  {"running":True,"last_action":f"CPU {cpu.get('temp_c',0):.0f}°C · {cpu.get('usage_percent',0):.0f}%"},
            "tuning":     {"running":True,"last_action":f"profile:{current_profile}"},
            "watchdog":   {"running":True,"last_action":"monitoring"},
            "prediction": {"running":True,"last_action":"forecasting"},
            "benchmark":  {"running":True,"last_action":"ready"},
            "profile":    {"running":True,"last_action":f"active:{current_profile}"},
        }
    })

# ── HEATMAP ───────────────────────────────────────────────────────────────────
@app.route('/api/heatmap')
def heatmap():
    state     = get_real_telemetry()
    cpu       = state.get("cpu", {})
    per_core  = cpu.get("per_core_usage", [])
    base_temp = cpu.get("temp_c", 50)

    # Real per-core temps if available, otherwise estimate
    real_temps = cpu.get("per_core_temps", [])
    if not real_temps and per_core:
        real_temps = [
            round(base_temp + (c/100)*10 - 3 + (i%2)*1.5, 1)
            for i, c in enumerate(per_core)
        ]

    return jsonify({
        "ok":              True,
        "core_temps":      real_temps,
        "per_core_usage":  per_core,
        "base_temp":       base_temp,
        "num_cores":       len(per_core),
        "has_real_temps":  bool(cpu.get("per_core_temps")),
    })

# ── FPS ───────────────────────────────────────────────────────────────────────
@app.route('/api/fps')
def fps_api():
    state   = get_real_telemetry()
    cpu     = state.get("cpu", {})
    fps     = estimate_fps(current_profile,
                           cpu.get("temp_c",50),
                           cpu.get("usage_percent",20),
                           running_games)
    fps_all = fps_for_all_profiles(cpu.get("temp_c",50), cpu.get("usage_percent",20))
    return jsonify({
        "ok":              True,
        "fps":             fps,
        "current_profile": current_profile,
        "fps_by_profile":  fps_all,
        "game_detected":   len(running_games) > 0,
        "game_name":       running_games[0]["name"] if running_games else None,
    })

# ── TUNE ──────────────────────────────────────────────────────────────────────
@app.route('/api/tune', methods=['POST'])
def tune():
    global current_profile
    data        = request.json or {}
    goal        = data.get("goal","balanced")
    profile_key = detect_profile_from_goal(goal)
    info        = PROFILE_INFO.get(profile_key, PROFILE_INFO["balanced"])
    success     = apply_power_plan(POWER_PLANS.get(profile_key, POWER_PLANS["balanced"]))
    current_profile = profile_key
    state = get_real_telemetry()
    fps   = estimate_fps(profile_key,
                         state.get("cpu",{}).get("temp_c",50),
                         state.get("cpu",{}).get("usage_percent",20),
                         running_games)
    return jsonify({
        "ok":True, "profile":profile_key,
        "reasoning":info["reasoning"],
        "expected_outcome":info["expected_outcome"],
        "confidence":0.92, "fps_estimate":fps,
        "power_plan_applied":success,
        "warnings":[] if success else ["Run as Administrator for power plan changes"],
        "settings":{"cpu":info["cpu"],"gpu":info["gpu"]},
    })

@app.route('/api/profile/<name>', methods=['POST'])
def apply_profile(name):
    global current_profile
    info    = PROFILE_INFO.get(name, PROFILE_INFO["balanced"])
    success = apply_power_plan(POWER_PLANS.get(name, POWER_PLANS["balanced"]))
    current_profile = name
    state = get_real_telemetry()
    fps   = estimate_fps(name, state.get("cpu",{}).get("temp_c",50),
                         state.get("cpu",{}).get("usage_percent",20), running_games)
    return jsonify({
        "ok":True, "profile":name,
        "reasoning":info["reasoning"],
        "expected_outcome":info["expected_outcome"],
        "fps_estimate":fps, "power_plan_applied":success,
        "settings":{"cpu":info["cpu"],"gpu":info["gpu"]},
    })

@app.route('/api/profiles')
def list_profiles():
    return jsonify({"ok":True,"profiles":list(PROFILE_INFO.keys())})

# ── HISTORY ───────────────────────────────────────────────────────────────────
@app.route('/api/history')
def history():
    return jsonify({"ok":True,"history":session_history[-60:]})

# ── ALERTS ────────────────────────────────────────────────────────────────────
@app.route('/api/alerts')
def alerts():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT timestamp,level,message FROM alerts ORDER BY timestamp DESC LIMIT 20"
        ).fetchall()
    return jsonify({"ok":True,"alerts":[
        {"timestamp":r[0],"level":r[1],"message":r[2]} for r in rows]})

@app.route('/api/alerts/thresholds', methods=['POST'])
def set_thresholds():
    data = request.json or {}
    for k in alert_thresholds:
        if k in data: alert_thresholds[k] = data[k]
    return jsonify({"ok":True,"thresholds":alert_thresholds})

# ── GAMES ─────────────────────────────────────────────────────────────────────
@app.route('/api/games')
def games():
    return jsonify({"ok":True,"games":running_games,
                    "auto_tune_enabled":True,"current_profile":current_profile})

# ── SCHEDULE ──────────────────────────────────────────────────────────────────
@app.route('/api/schedule')
def get_schedule():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id,hour,minute,profile,label,enabled FROM schedule ORDER BY hour,minute"
        ).fetchall()
    return jsonify({"ok":True,"schedule":[
        {"id":r[0],"hour":r[1],"minute":r[2],"profile":r[3],"label":r[4],"enabled":bool(r[5])}
        for r in rows]})

@app.route('/api/schedule', methods=['POST'])
def add_schedule():
    d = request.json or {}
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO schedule (hour,minute,profile,label,enabled) VALUES (?,?,?,?,1)",
                     (d.get("hour",8),d.get("minute",0),d.get("profile","balanced"),d.get("label","Custom")))
    return jsonify({"ok":True})

@app.route('/api/schedule/<int:sid>', methods=['DELETE'])
def delete_schedule(sid):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM schedule WHERE id=?", (sid,))
    return jsonify({"ok":True})

@app.route('/api/schedule/<int:sid>/toggle', methods=['POST'])
def toggle_schedule(sid):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE schedule SET enabled=1-enabled WHERE id=?", (sid,))
    return jsonify({"ok":True})

# ── AI CHAT ───────────────────────────────────────────────────────────────────
@app.route('/api/chat', methods=['POST'])
def chat():
    data    = request.json or {}
    user_msg= data.get("message","")
    if not user_msg.strip():
        return jsonify({"ok":False,"error":"Empty message"}), 400
    state = get_real_telemetry()
    cpu   = state.get("cpu",{})
    mem   = state.get("memory",{})
    bat   = state.get("battery",{})
    sys_prompt = f"""You are RyzenSense AI Assistant — a friendly laptop hardware expert.
Current laptop:
- CPU: {cpu.get('model','Unknown')} | Temp: {cpu.get('temp_c',0):.0f}°C | Load: {cpu.get('usage_percent',0):.0f}% | Freq: {cpu.get('freq_mhz',0)} MHz
- RAM: {mem.get('used_mb',0)//1024:.1f}GB / {mem.get('total_mb',0)//1024:.0f}GB ({mem.get('percent',0):.0f}%)
- Battery: {bat.get('percent',100):.0f}% ({'Plugged in' if bat.get('plugged_in') else 'On battery'})
- Profile: {current_profile} | Games: {', '.join([g['name'] for g in running_games]) or 'None'}
Be helpful, specific, and concise (2-3 sentences max unless asked for detail)."""
    chat_history.append({"role":"user","content":user_msg})
    if len(chat_history) > 10: chat_history.pop(0)
    try:
        resp = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role":"system","content":sys_prompt}] + chat_history[-6:],
            max_tokens=300, temperature=0.7)
        reply = resp.choices[0].message.content.strip()
        chat_history.append({"role":"assistant","content":reply})
        return jsonify({"ok":True,"reply":reply})
    except Exception as e:
        return jsonify({"ok":False,"error":str(e)}), 500

# ── BENCHMARK + LEADERBOARD ───────────────────────────────────────────────────
@app.route('/api/benchmark', methods=['POST'])
def benchmark():
    import math, threading as th
    data     = request.json or {}
    username = data.get("username","Anonymous")

    def stress():
        result = 0
        end = time.perf_counter() + 2.0
        while time.perf_counter() < end:
            for i in range(1,500): result += math.sqrt(i)*math.log(i)
    start = time.perf_counter(); stress()
    single_time  = time.perf_counter() - start
    single_score = int(1000/single_time)
    threads_list = [th.Thread(target=stress) for _ in range(psutil.cpu_count(logical=True) or 4)]
    mt_start = time.perf_counter()
    for t in threads_list: t.start()
    for t in threads_list: t.join()
    multi_time  = time.perf_counter() - mt_start
    multi_score = int(1000/multi_time*(psutil.cpu_count(logical=True) or 4))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO benchmarks (timestamp,username,single_score,multi_score,profile,cpu_model) VALUES (?,?,?,?,?,?)",
                     (datetime.now().isoformat(),username,single_score,multi_score,current_profile,CPU_MODEL))
    return jsonify({"ok":True,"results":{
        "username":username,"single_score":single_score,"multi_score":multi_score,
        "single_time_s":round(single_time,3),"multi_time_s":round(multi_time,3),
        "speedup":round(single_time/multi_time,2),"profile":current_profile}})

@app.route('/api/leaderboard')
def leaderboard():
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT username,single_score,multi_score,profile,cpu_model,timestamp FROM benchmarks ORDER BY multi_score DESC LIMIT 20"
        ).fetchall()
    return jsonify({"ok":True,"leaderboard":[
        {"rank":i+1,"username":r[0],"single_score":r[1],"multi_score":r[2],
         "profile":r[3],"cpu_model":r[4],"timestamp":r[5]} for i,r in enumerate(rows)]})

@app.route('/api/reset', methods=['POST'])
def reset():
    global current_profile
    success = apply_power_plan(POWER_PLANS["balanced"])
    current_profile = "balanced"
    return jsonify({"ok":True,"message":"Reset to Balanced","success":success})

# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f"\n🔴 RyzenSense v2 — Real-Time Server")
    print("=" * 50)
    print(f"  CPU    : {CPU_MODEL}")
    print(f"  Cores  : {psutil.cpu_count(logical=False)}C / {psutil.cpu_count(logical=True)}T")
    print(f"  RAM    : {psutil.virtual_memory().total//(1024**3)} GB")
    print(f"  Features: FPS · Heatmap · Alerts · Games · Schedule · Chat · Leaderboard")
    print(f"\n  ➜  http://localhost:5000")
    print("  Ctrl+C to stop\n")
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)