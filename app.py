"""Telegram front door. Part 1 commands: /data, /baseline, /status, /help."""
import os, threading
import requests
from flask import Flask, request
import config as cfg
import data
import backtest as bt

app = Flask(__name__)
lock = threading.Lock()
COMMANDS = [("data", "Download gold history and show what was loaded"),
            ("baseline", "Run the benchmark test"),
            ("status", "Settings and saved data"),
            ("help", "List commands")]

def tg(text):
    requests.post(f"https://api.telegram.org/bot{cfg.TOKEN}/sendMessage",
                  json={"chat_id": cfg.CHAT_ID, "text": text}, timeout=15)

def cmd_data():
    tg("Downloading gold data. The long history takes 1-2 minutes...")
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

def cmd_status():
    files = [n for n in ("spot", "futures") if data.read(n) is not None]
    tg(f"Spread ${cfg.SPREAD:g}, history {cfg.HISTORY_DAYS} days, horizon {cfg.HORIZON} candles\n"
       f"Twelve Data key set: {'yes' if cfg.TWELVE_KEY else 'no'}\n"
       f"Saved data: {', '.join(files) if files else 'none yet'}")

def handle(text):
    cmd = text.strip().split()[0].split("@")[0].lower()
    try:
        if cmd in ("/start", "/help"):
            tg("Commands:\n" + "\n".join(f"/{c} - {d}" for c, d in COMMANDS))
        elif cmd == "/status":
            cmd_status()
        elif cmd in ("/data", "/baseline"):
            if lock.locked():
                tg("Busy with another task, try again in a minute.")
                return
            with lock:
                cmd_data() if cmd == "/data" else cmd_baseline()
        else:
            tg("Unknown command. Send /help")
    except Exception as e:
        tg(f"Error: {e}")

def authed():
    return bool(cfg.SCAN_KEY) and request.args.get("key") == cfg.SCAN_KEY

@app.route("/")
def home():
    return "Gold bot (Part 1) is running."

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
