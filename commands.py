"""All Telegram commands except the update tools (those live in admin.py, which cannot be
replaced from Telegram, so you can always roll back)."""
import threading
import requests
import config as cfg
import data
import backtest as bt
import setups
import live

MENU = [("results", "Live win/loss record of sent alerts"),
        ("status", "Live scanner and settings"),
        ("scan", "Check for a new candle now"),
        ("pause", "Pause new alerts"),
        ("resume", "Resume alerts"),
        ("test", "Test a setup: /test breakout 3 trail"),
        ("set", "Change a setting: /set TRAIL 1.5"),
        ("settings", "Show all settings"),
        ("data", "Download gold history"),
        ("baseline", "Run the benchmark test"),
        ("update", "Replace a code file (paste it, then /done)"),
        ("patch", "Apply a small code change"),
        ("rollback", "Undo the last update of a file"),
        ("version", "Show running code versions"),
        ("help", "List commands")]
USAGE_TEST = "Usage: /test pullback|breakout|vwap|all [target R, e.g. 3] [trail]"
_heavy = globals().get("_heavy") or threading.Lock()

def tg(text):
    requests.post(f"https://api.telegram.org/bot{cfg.TOKEN}/sendMessage",
                  json={"chat_id": cfg.CHAT_ID, "text": text[:4000]}, timeout=15)

def help_text():
    return ("Commands:\n" + "\n".join(f"/{c} - {d}" for c, d in MENU)
            + "\n\nSmall code change: send one message like\n/patch setups.py\n<<<<\nold text\n"
              "====\nnew text\n>>>>\n\nWhole file: /update setups.py, paste the parts one message "
              "each, then /done.")

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
    rr, manage = None, False
    for a in args[1:]:
        if a.lower() == "trail":
            manage = True
        else:
            try:
                rr = float(a)
            except ValueError:
                tg(USAGE_TEST)
                return
    if key != "all" and key not in setups.SETUPS:
        tg(USAGE_TEST)
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
        trades = bt.simulate(d, fn(d, rr), manage=manage)
        target = "target = VWAP" if k == "vwap" else f"target {rr or cfg.RR:g}R"
        if manage:
            target += f", breakeven at 1R then trailing {cfg.TRAIL:g}R"
        text = bt.report(f"{name} on {dataset} ({target})", trades)
        bd = bt.breakdown(trades)
        parts.append(text + ("\n" + bd if bd else ""))
        summary.append(f"{name}: {'PASS' if bt.passes(trades) else 'FAIL'}")
    if len(summary) > 1:
        parts.append("Summary\n" + "\n".join(summary))
    tg("\n\n".join(parts))

def cmd_status():
    files = [n for n in ("spot", "futures") if data.read(n) is not None]
    tg(f"{live.status_text()}\n\n"
       f"Test settings: spread ${cfg.SPREAD:g}, history {cfg.HISTORY_DAYS} days, test target "
       f"{cfg.RR:g}R, trailing {cfg.TRAIL:g}R, breakout trend filter "
       f"{'on' if cfg.BRK_TREND else 'off'}\n"
       f"Twelve Data key set: {'yes' if cfg.TWELVE_KEY else 'no'}, saved data: "
       f"{', '.join(files) if files else 'none'}")

def cmd_set(args):
    if len(args) < 2:
        tg("Usage: /set NAME VALUE, for example /set TRAIL 1.5\nSend /settings to see all names.")
        return
    try:
        val = cfg.set_value(args[0], " ".join(args[1:]))
    except Exception as e:
        tg(f"Not changed: {e}")
        return
    tg(f"{args[0].upper()} is now {val} (saved, it survives restarts).")

def handle(text):
    parts = text.strip().split()
    cmd, args = parts[0].split("@")[0].lower(), parts[1:]
    try:
        if cmd in ("/start", "/help"):
            tg(help_text())
        elif cmd == "/status":
            cmd_status()
        elif cmd == "/settings":
            tg("\n".join(f"{k} = {getattr(cfg, k)}" for k in cfg.TUNABLE))
        elif cmd == "/set":
            cmd_set(args)
        elif cmd == "/results":
            tg(live.results_text())
        elif cmd == "/pause":
            live.set_paused(True)
            tg("New alerts paused. Open trades are still tracked. Send /resume to restart alerts.")
        elif cmd == "/resume":
            live.set_paused(False)
            tg("Alerts resumed.")
        elif cmd == "/scan":
            msg, _ = live.scan()
            live.state["msg"] = msg
            tg(f"Scan: {msg}")
        elif cmd in ("/data", "/baseline", "/test"):
            if _heavy.locked():
                tg("Busy with another task, try again in a minute.")
                return
            with _heavy:
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
