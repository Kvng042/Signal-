"""Front door. This file should never need changing: everything else can be updated from Telegram."""
import os, threading
import requests
from flask import Flask, request
import admin
admin.setup_paths()  # must run before the other imports
import config as cfg
import commands
import live

app = Flask(__name__)

@app.route("/")
def home():
    return f"Gold bot is running. Last scan: {live.state['msg']}"

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    if not cfg.SCAN_KEY or request.headers.get("X-Telegram-Bot-Api-Secret-Token") != cfg.SCAN_KEY:
        return "forbidden", 403
    msg = (request.get_json(silent=True) or {}).get("message") or {}
    text = msg.get("text", "")
    if text and str((msg.get("chat") or {}).get("id")) == str(cfg.CHAT_ID):
        try:
            if admin.handle(text, msg.get("message_id", 0)):
                return "ok"
        except Exception as e:
            admin.say(f"Update tool error: {str(e)[:300]}")
            return "ok"
        if text.startswith("/"):
            threading.Thread(target=commands.handle, args=(text,), daemon=True).start()
    return "ok"

@app.route("/setup")
def setup_route():
    if not cfg.SCAN_KEY or request.args.get("key") != cfg.SCAN_KEY:
        return "forbidden", 403
    base = f"https://api.telegram.org/bot{cfg.TOKEN}"
    url = request.url_root.replace("http://", "https://") + "telegram"
    r = requests.post(base + "/setWebhook", timeout=15,
                      json={"url": url, "secret_token": cfg.SCAN_KEY,
                            "allowed_updates": ["message"], "drop_pending_updates": True})
    requests.post(base + "/setMyCommands", timeout=15,
                  json={"commands": [{"command": c, "description": d} for c, d in commands.MENU]})
    return r.text

live.start()  # background scanner

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
