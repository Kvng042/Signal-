"""Telegram front door. Commands: /data, /baseline, /test, /status, /help."""
import os, threading
import requests
from flask import Flask, request
import config as cfg
import data
import backtest as bt
import setups

app = Flask(__name__)
lock = threading.Lock()
COMMANDS = [("data", "Download gold history and show what was loaded"),
            ("baseline", "Run the benchmark test"),
            ("test", "Test a setup: /test pullback, breakout, vwap or all"),
            ("status", "Settings and saved data"),
            ("help", "List commands")]

def tg(text):
    requests.post(f"https://api.telegram.org/bot{cfg.TOKEN}/sendMessage",
                  json={"chat_id": cfg.CHAT_ID, "text": text}, timeout=15)

def cmd_data():
    tg("Downloading gold data. The long history takes a few minutes...")
    fut, msg_f = data.load_futures()
    spot, msg_s = data.load_spot()
    lines = [data.describe(spot, "Spot XAU/USD (no volume)") + f"  [{msg_s}]",
             data.describe(fut, "Futures GC=F (with volume)") + f"  [{msg_f}]"]
    b = data.basis(spot, fut)
    if b is not None:
        lines.append(f"Futures minus spot price: {b:+.2f}")
    tg("\n".join(lines))

def cmd_baseline():
    df, label = data.read("spot"), "spot XAU/USD"
    if df is None or df.empty:
        df, label = data.read("futures"), "futures GC=F"
    if df is None or df.empty:
        tg("No data yet. Send /data first.")
        return
    df = bt.add_atr(df)
    trades = bt.simulate(df, bt.baseline_signals(df))
    tg(bt.report(f"Baseline test on {label} (entries at fixed times, 1:2)", trades))

def cmd_test(args):
    key = args[0].lower() if args else "all"
    try:
        rr = float(args[1]) if len(args) > 1 else None
    except ValueError:
        tg("Usage: /test pullback|breakout|vwap|all [target R, e.g. 1.5]")
        return
    if key != "all" and key not in setups.SETUPS:
        tg("Usage: /test pullback|breakout|vwap|all [target R, e.g. 1.5]")
        return
    keys = list(setups.SETUPS) if key == "all" else [key]
    parts, summary = [], []
    for k in keys:
        name, dataset, fn = setups.SETUPS[k]
        df = data.read(dataset)
        if df is None or df.empty:
            parts.append(f"{name}: no {dataset} data, send /data first.")
            continue
        d = setups.prepare(df)
        trades = bt.simulate(d, fn(d, rr))
        target = "target = VWAP" if k == "vwap" else f"target {rr or cfg.RR:g}R"
        text = bt.report(f"{name} on {dataset} ({target})", trades)
        bd = bt.breakdown(trades)
        parts.append(text + ("\n" + bd if bd else ""))
        summary.append(f"{name}: {'PASS' if bt.passes(trades) else 'FAIL'}")
    if len(summary) > 1:
        parts.append("Summary\n" + "\n".join(summary))
    tg("\n\n".join(parts))

def cmd_status():
    files = [n for n in ("spot", "futures") if data.read(n) is not None]
    tg(f"Spread ${cfg.SPREAD:g}, history {cfg.HISTORY_DAYS} days, horizon {cfg.HORIZON} candles\n"
       f"Target {cfg.RR:g}R, min stop ${cfg.MIN_STOP:g}, VWAP stretch {cfg.STRETCH:g} ATR, "
       f"breakout trend filter {'on' if cfg.BRK_TREND else 'off'}\n"
       f"Twelve Data key set: {'yes' if cfg.TWELVE_KEY else 'no'}\n"
       f"Saved data: {', '.join(files) if files else 'none yet'}")

def handle(text):
    parts = text.strip().split()
    cmd, args = parts[0].split("@")[0].lower(), parts[1:]
    try:
        if cmd in ("/start", "/help"):
            tg("Commands:\n" + "\n".join(f"/{c} - {d}" for c, d in COMMANDS))
        elif cmd == "/status":
            cmd_status()
        elif cmd in ("/data", "/baseline", "/test"):
            if lock.locked():
                tg("Busy with another task, try again in a minute.")
                return
            with lock:
                if cmd == "/data":
                    cmd_data()
                elif cmd == "/baseline":
                    cmd_baseline()
                else:
                    cmd_test(args)
        else:
            tg("Unknown command. Send /help")
    except Exception as e:
        tg(f"Error: {e}")

def authed():
    return bool(cfg.SCAN_KEY) and request.args.get("key") == cfg.SCAN_KEY

@app.route("/")
def home():
    return "Gold bot (Part 3a) is running."

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    if not cfg.SCAN_KEY or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != cfg.SCAN_KEY:
        return "forbidden", 403
    msg = (request.get_json(silent=True) or {}).get("message") or {}
    text = msg.get("text", "")
    if str((msg.get("chat") or {}).get("id")) == str(cfg.CHAT_ID) and text.startswith("/"):
        threading.Thread(target=handle, args=(text,), daemon=True).start()
    return "ok"

@app.route("/setup")
def setup_route():
    if not authed():
        return "forbidden", 403
    base = f"https://api.telegram.org/bot{cfg.TOKEN}"
    url = request.url_root.replace("http://", "https://") + "telegram"
    r = requests.post(base + "/setWebhook", timeout=15,
                      json={"url": url, "secret_token": cfg.SCAN_KEY,
                            "allowed_updates": ["message"], "drop_pending_updates": True})
    requests.post(base + "/setMyCommands", timeout=15,
                  json={"commands": [{"command": c, "description": d} for c, d in COMMANDS]})
    return r.text

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
