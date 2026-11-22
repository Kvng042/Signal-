"""Live alerts: scans gold after every 5-minute candle, sends breakout alerts, manages open
trades (breakeven, trailing stop) and reports each result. State is saved to a file so a
restart does not lose open trades."""
import json, os, threading, time
import pandas as pd
import requests
import config as cfg
import data
import setups

try:
    import fcntl
except ImportError:
    fcntl = None

STATE_FILE = os.path.join(cfg.DATA_DIR, "live_state.json")
PERSIST = ("open", "log", "next_id", "tally", "day", "day_r", "halted", "paused", "last_ts")
state = {"open": [], "log": [], "next_id": 0,
         "tally": {"win": 0, "loss": 0, "flat": 0, "total_r": 0.0},
         "day": None, "day_r": 0.0, "halted": False, "paused": False, "last_ts": None,
         "msg": "not started", "calls": 0, "calls_day": None, "retries": 0, "next_scan": 0.0}
lock = threading.Lock()
_lockfile = None

def save():
    try:
        os.makedirs(cfg.DATA_DIR, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump({k: state[k] for k in PERSIST}, f)
    except Exception:
        pass

def load():
    try:
        with open(STATE_FILE) as f:
            saved = json.load(f)
        for k in PERSIST:
            if k in saved:
                state[k] = saved[k]
    except Exception:
        pass

load()

def tg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{cfg.TOKEN}/sendMessage",
                      json={"chat_id": cfg.CHAT_ID, "text": text}, timeout=15)
    except Exception:
        pass

def px(x):
    return f"{x + cfg.PRICE_OFFSET:.2f}"

def ng(ts):
    """Closing time of the candle that started at ts, in Nigeria time (UTC+1)."""
    return (ts + pd.Timedelta(minutes=65)).strftime("%H:%M")

def seconds_to_slot():
    now = time.time()
    return 300 - (now % 300) + 20

def alert_text(t, ts):
    buy = t["side"] == "BUY"
    sgn = 1 if buy else -1
    rh, rl = t["rng"]
    trend = f", with the 1h trend {'up' if buy else 'down'}" if cfg.BRK_TREND else ""
    return (f"🧪 UNPROVEN, FORWARD TEST #{t['id']}\n"
            f"{'🟢' if buy else '🔴'} XAUUSD {t['side']} (5m)\n"
            f"Setup: {t['setup']}\n"
            f"Entry: {px(t['entry'])}\n"
            f"Stop loss: {px(t['stop'])}\n"
            f"Max target: {px(t['tp'])} ({cfg.LIVE_RR:g}R)\n"
            f"Skip if price already beyond: {px(t['entry'] + sgn * 0.25 * t['risk'])}\n"
            f"Management: at {px(t['entry'] + sgn * t['risk'])} (+1R) move the stop to breakeven, "
            f"then trail {cfg.TRAIL:g}R behind the best price. I will message you at each step.\n"
            f"Reason: a strong candle closed {'above' if buy else 'below'} the first-30-minute range "
            f"({px(rl)} to {px(rh)}) of the {t['setup'].replace('Breakout ', '')} session{trend}.\n"
            f"Candle closed: {ng(ts)} Nigeria time")

def finish(t, outcome, exit_price, ts, msgs):
    buy = t["side"] == "BUY"
    gross = (exit_price - t["entry"]) / t["risk"] if buy else (t["entry"] - exit_price) / t["risk"]
    r = float(gross - cfg.SPREAD / t["risk"])
    head = f"#{t['id']} {t['side']} {t['setup']}"
    if outcome == "WIN":
        txt = f"✅ WIN {head}\nTarget {px(t['tp'])} hit: {r:+.2f}R after spread."
    elif outcome == "LOSS":
        txt = f"❌ LOSS {head}\nStop {px(exit_price)} hit: {r:+.2f}R after spread."
    elif outcome == "BE":
        txt = f"🟡 BREAKEVEN {head}\nStopped at entry: {r:+.2f}R after spread."
    elif outcome == "TRAIL":
        txt = f"🟢 TRAIL EXIT {head}\nStopped out in profit at {px(exit_price)}: {r:+.2f}R after spread."
    else:
        txt = (f"⏱ TIMEOUT {head}\nClosed after {cfg.HORIZON * 5 // 60} hours at {px(exit_price)}: "
               f"{r:+.2f}R. Treat it as closed.")
    msgs.append(f"{txt}\nClosed around {ng(ts)} Nigeria time.")
    tally = state["tally"]
    tally["win" if r > 0.1 else "loss" if r < -0.1 else "flat"] += 1
    tally["total_r"] = float(tally["total_r"] + r)
    state["day_r"] = float(state["day_r"] + r)
    state["log"] = (state["log"] + [dict(id=t["id"], side=t["side"], setup=t["setup"],
                                         outcome=outcome, r=round(r, 2))])[-30:]

def advance(t, d, msgs):
    """Apply the candles that arrived since the last scan to an open trade. True if closed."""
    idx = d.index
    H, L, C = d.High.to_numpy(), d.Low.to_numpy(), d.Close.to_numpy()
    buy = t["side"] == "BUY"
    pos = idx.searchsorted(pd.Timestamp(t["last"]), side="right")
    for j in range(pos, len(d)):
        t["last"] = idx[j].isoformat()
        t["n"] += 1
        if buy:
            stopped, hit = L[j] <= t["cur"], H[j] >= t["tp"]
        else:
            stopped, hit = H[j] >= t["cur"], L[j] <= t["tp"]
        if stopped:  # a candle touching both stop and target counts as a stop
            c, e = t["cur"], t["entry"]
            if c == e:
                outcome = "BE"
            elif (c < e) == buy:
                outcome = "LOSS"
            else:
                outcome = "TRAIL"
            finish(t, outcome, c, idx[j], msgs)
            return True
        if hit:
            finish(t, "WIN", t["tp"], idx[j], msgs)
            return True
        before = t["cur"]
        if buy:
            t["best"] = max(t["best"], H[j])
            if t["best"] - t["entry"] >= t["risk"]:
                t["cur"] = max(t["cur"], t["entry"], t["best"] - cfg.TRAIL * t["risk"])
        else:
            t["best"] = min(t["best"], L[j])
            if t["entry"] - t["best"] >= t["risk"]:
                t["cur"] = min(t["cur"], t["entry"], t["best"] + cfg.TRAIL * t["risk"])
        if t["cur"] != before:
            locked = ((t["cur"] - t["entry"]) if buy else (t["entry"] - t["cur"])) / t["risk"]
            lock_txt = "breakeven" if locked < 0.05 else f"locks +{locked:.1f}R"
            if not t["be_sent"]:
                t["be_sent"], t["msg_stop"] = True, t["cur"]
                msgs.append(f"🔔 #{t['id']} reached +1R. Move your stop to {px(t['cur'])} ({lock_txt}).")
            elif abs(t["cur"] - t["msg_stop"]) >= 0.5 * t["risk"]:
                t["msg_stop"] = t["cur"]
                msgs.append(f"🔔 #{t['id']} trail your stop to {px(t['cur'])} ({lock_txt}).")
        if t["n"] >= cfg.HORIZON:
            finish(t, "TIMEOUT", C[j], idx[j], msgs)
            return True
    return False

def _scan():
    now = pd.Timestamp.now(tz="UTC")
    today = now.strftime("%Y-%m-%d")
    if state["calls_day"] != today:
        state["calls_day"], state["calls"] = today, 0
    if state["day"] != today:
        state["day"], state["day_r"], state["halted"] = today, 0.0, False
    if state["calls"] >= 780:
        return "daily Twelve Data request limit reached", 3600
    state["calls"] += 1
    d, msg = data.load_recent()
    if d is None or d.empty:
        return f"no data: {msg}", 60
    ts = d.index[-1]
    age = (now - ts).total_seconds()
    if age > 1800:
        state["retries"] = 0
        return "market closed or data stale", 900
    if state["last_ts"] == ts.isoformat():
        state["retries"] += 1
        return "no new candle", (40 if state["retries"] <= 3 else seconds_to_slot())
    state["retries"] = 0
    d = setups.prepare(d)
    msgs, still = [], []
    for t in state["open"]:
        if not advance(t, d, msgs):
            still.append(t)
    state["open"] = still
    state["last_ts"] = ts.isoformat()
    if state["day_r"] <= -cfg.DAILY_LOSS_R and not state["halted"]:
        state["halted"] = True
        msgs.append(f"🛑 Daily loss limit reached ({state['day_r']:+.1f}R). "
                    "No new alerts until tomorrow (UTC).")
    sent = 0
    if not state["paused"] and not state["halted"] and age <= 720:
        for s in setups.breakout(d, cfg.LIVE_RR):
            if s["i"] != len(d) - 1:
                continue
            entry, stop = float(s["entry"]), float(s["stop"])
            t = dict(id=state["next_id"] + 1, side=s["side"], setup=s["setup"], entry=entry,
                     stop=stop, tp=float(s["tp"]), risk=abs(entry - stop), cur=stop, best=entry,
                     n=0, last=ts.isoformat(), be_sent=False, msg_stop=stop,
                     rng=[float(s["rng"][0]), float(s["rng"][1])])
            state["next_id"] += 1
            state["open"].append(t)
            msgs.append(alert_text(t, ts))
            sent += 1
    for m in msgs:
        tg(m)
    save()
    return f"scanned {ts:%H:%M} UTC, new alerts: {sent}", seconds_to_slot()

def scan():
    with lock:
        return _scan()

def loop():
    while True:
        try:
            msg, wait = scan()
            state["msg"] = msg
        except Exception as e:
            state["msg"], wait = f"error: {e}", 60
        state["next_scan"] = time.time() + wait
        time.sleep(max(5, wait))

def start():
    """Starts the background scanner (only one process can hold the scanner lock)."""
    global _lockfile
    if not cfg.LIVE:
        state["msg"] = "live scanning is off (LIVE=0)"
        return
    try:
        os.makedirs(cfg.DATA_DIR, exist_ok=True)
        if fcntl:
            _lockfile = open(os.path.join(cfg.DATA_DIR, "scanner.lock"), "w")
            fcntl.flock(_lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        return  # another process already runs the scanner
    threading.Thread(target=loop, daemon=True).start()

def set_paused(flag):
    with lock:
        state["paused"] = flag
        save()

def status_text():
    with lock:
        load()
        left = max(0, int(state["next_scan"] - time.time()))
        return (f"Live scanning: {'on' if cfg.LIVE else 'off'}"
                f"{', PAUSED' if state['paused'] else ''}{', halted for today' if state['halted'] else ''}\n"
                f"Last scan: {state['msg']}\n"
                f"Next scan in about {left} seconds\n"
                f"Twelve Data requests today: {state['calls']} of about 800\n"
                f"Open trades: {len(state['open'])}, today {state['day_r']:+.2f}R\n"
                f"Sessions: {cfg.SESSIONS_ON}, max target {cfg.LIVE_RR:g}R, "
                f"daily loss limit {cfg.DAILY_LOSS_R:g}R")

def results_text():
    with lock:
        load()
        t = state["tally"]
        done = t["win"] + t["loss"] + t["flat"]
        lines = ["Live results (forward test of unproven alerts):",
                 f"{t['win']} wins, {t['loss']} losses, {t['flat']} flat, net {t['total_r']:+.2f}R after spread"
                 + (f" ({100 * t['win'] / done:.0f}% wins)" if done else ""),
                 f"Today: {state['day_r']:+.2f}R" + (" (halted for the day)" if state["halted"] else ""),
                 f"Open trades: {len(state['open'])}"]
        for tr in state["open"]:
            lines.append(f"#{tr['id']} {tr['side']} {tr['setup']}, stop now {px(tr['cur'])}")
        if state["log"]:
            lines += ["", "Last results:"]
            for r in state["log"][-8:]:
                lines.append(f"#{r['id']} {r['side']} {r['setup']}: {r['outcome']} {r['r']:+.2f}R")
    return "\n".join(lines)
