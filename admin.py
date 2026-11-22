"""Update tools. This file cannot be replaced from Telegram: it is the safety net.
Updated files are saved in data/code and loaded instead of the built-in copies.
The previous version of every file is kept so /rollback always works."""
import importlib, os, sys, time
import requests

DATA_DIR = os.environ.get("DATA_DIR", "data")
CODE_DIR = os.path.join(DATA_DIR, "code")
BACKUP_DIR = os.path.join(DATA_DIR, "code_backup")
HERE = os.path.dirname(os.path.abspath(__file__))
ALLOWED = ["config.py", "data.py", "backtest.py", "setups.py", "live.py", "commands.py"]
ORDER = ["config", "data", "backtest", "setups", "live", "commands"]
MAX_BYTES = 300000
pending = {}

PATCH_HELP = ("Format:\n/patch setups.py\n<<<<\nold text (copied exactly)\n====\nnew text\n>>>>\n"
              "You can put several <<<< ... >>>> blocks in one message.")

def setup_paths():
    """Call first. Puts the update folder ahead of the built-in files on the import path."""
    os.makedirs(CODE_DIR, exist_ok=True)
    if CODE_DIR not in sys.path:
        sys.path.insert(0, CODE_DIR)
    sys.dont_write_bytecode = True

def say(text):
    try:
        requests.post(f"https://api.telegram.org/bot{os.environ.get('TELEGRAM_TOKEN', '')}/sendMessage",
                      json={"chat_id": os.environ.get("CHAT_ID", ""), "text": text[:4000]}, timeout=15)
    except Exception:
        pass

def _current_path(name):
    p = os.path.join(CODE_DIR, name)
    return p if os.path.exists(p) else os.path.join(HERE, name)

def _read(name):
    with open(_current_path(name), encoding="utf-8") as f:
        return f.read()

def _write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, path)

def reload_all():
    importlib.invalidate_caches()
    for m in ORDER:
        mod = sys.modules.get(m)
        if mod is None:
            importlib.import_module(m)
        else:
            importlib.reload(mod)

def install(name, text):
    if name not in ALLOWED:
        return "Not allowed. Files you can update: " + ", ".join(ALLOWED)
    if len(text.encode("utf-8")) > MAX_BYTES:
        return "Rejected: file too large."
    try:
        compile(text, name, "exec")
    except SyntaxError as e:
        return (f"Rejected: syntax error in {name} at line {e.lineno}: {e.msg}. Nothing was changed. "
                "If you pasted in parts, check that no part was cut in the middle of a function.")
    os.makedirs(CODE_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    target = os.path.join(CODE_DIR, name)
    had_override = os.path.exists(target)
    try:
        old = _read(name)
    except Exception:
        old = None
    if old is not None:
        _write(os.path.join(BACKUP_DIR, f"{name}.{int(time.time())}"), old)
    _write(target, text)
    try:
        reload_all()
    except Exception as e:
        if had_override and old is not None:
            _write(target, old)
        elif os.path.exists(target):
            os.remove(target)
        try:
            reload_all()
        except Exception:
            pass
        return (f"Rejected: {name} failed to load ({type(e).__name__}: {str(e)[:200]}). "
                "The previous version was restored.")
    return f"Updated {name} ({text.count(chr(10)) + 1} lines) and reloaded. Send /version to check."

def _backups(name):
    if not os.path.isdir(BACKUP_DIR):
        return []
    found = []
    for f in os.listdir(BACKUP_DIR):
        stem, _, stamp = f.rpartition(".")
        if stem == name and stamp.isdigit():
            found.append((int(stamp), f))
    return sorted(found)

def rollback(name):
    if name not in ALLOWED:
        return "Not allowed. Files: " + ", ".join(ALLOWED)
    backups = _backups(name)
    if not backups:
        return f"No earlier version of {name} is saved."
    stamp, fname = backups[-1]
    path = os.path.join(BACKUP_DIR, fname)
    with open(path, encoding="utf-8") as f:
        old = f.read()
    _write(os.path.join(CODE_DIR, name), old)
    os.remove(path)
    try:
        reload_all()
    except Exception as e:
        return f"Restored {name} but reloading failed: {type(e).__name__}: {str(e)[:200]}"
    return f"Rolled back {name} to the version saved at {time.strftime('%Y-%m-%d %H:%M', time.gmtime(stamp))} UTC."

def version_text():
    lines = []
    for name in ALLOWED:
        ov = os.path.join(CODE_DIR, name)
        try:
            if os.path.exists(ov):
                lines.append(f"{name}: UPDATED {time.strftime('%Y-%m-%d %H:%M', time.gmtime(os.path.getmtime(ov)))} "
                             f"UTC, {os.path.getsize(ov)} bytes")
            else:
                lines.append(f"{name}: built-in, {os.path.getsize(os.path.join(HERE, name))} bytes")
        except Exception:
            lines.append(f"{name}: missing")
    return "\n".join(lines)

def parse_patch(body):
    blocks, mode, old, new = [], None, [], []
    for ln in body.split("\n"):
        s = ln.strip()
        if s == "<<<<":
            mode, old, new = "old", [], []
        elif s == "====" and mode == "old":
            mode = "new"
        elif s == ">>>>" and mode == "new":
            blocks.append(("\n".join(old), "\n".join(new)))
            mode = None
        elif mode == "old":
            old.append(ln)
        elif mode == "new":
            new.append(ln)
    return blocks

def handle(text, mid=0):
    """Returns True if the message was an update-tool message (or a pasted piece of code)."""
    t = text.strip()
    words = t.split("\n", 1)[0].split()
    cmd = words[0].split("@")[0].lower() if words and words[0].startswith("/") else ""
    args = words[1:]
    if pending and time.time() - pending["started"] > 900:
        pending.clear()
    if cmd == "/update":
        name = args[0] if args else ""
        if name not in ALLOWED:
            say("Usage: /update filename\nFiles: " + ", ".join(ALLOWED))
            return True
        pending.clear()
        pending.update(name=name, chunks=[], started=time.time())
        say(f"Ready for {name}. Paste the code in parts, one message each, in order. "
            "Send /done when finished, or /cancel.")
        return True
    if cmd == "/done":
        if not pending:
            say("Nothing to finish. Start with /update filename.")
            return True
        time.sleep(2)  # let the last pasted message arrive
        chunks = sorted(pending["chunks"], key=lambda c: c[0])
        name = pending["name"]
        pending.clear()
        code = "\n\n".join(c[1] for c in chunks)
        say(install(name, code) if code.strip() else "No code was received.")
        return True
    if cmd == "/cancel":
        pending.clear()
        say("Cancelled.")
        return True
    if cmd == "/patch":
        if not args or "\n" not in t:
            say(PATCH_HELP)
            return True
        name = args[0]
        if name not in ALLOWED:
            say("Not allowed. Files: " + ", ".join(ALLOWED))
            return True
        blocks = parse_patch(t.split("\n", 1)[1])
        if not blocks:
            say("No change blocks found.\n" + PATCH_HELP)
            return True
        code = _read(name)
        for i, (old, new) in enumerate(blocks, 1):
            n = code.count(old)
            if not old.strip() or n != 1:
                say(f"Block {i} not applied: the old text was found {n} times (it must be found exactly "
                    "once). Nothing was changed.")
                return True
            code = code.replace(old, new)
        say(install(name, code))
        return True
    if cmd == "/fetch":
        if len(args) != 2 or not args[1].startswith("https://"):
            say("Usage: /fetch filename https://link-to-raw-code")
            return True
        try:
            r = requests.get(args[1], timeout=30)
            r.raise_for_status()
            say(install(args[0], r.text))
        except Exception as e:
            say(f"Could not fetch: {str(e)[:150]}")
        return True
    if cmd == "/rollback":
        say(rollback(args[0]) if args else "Usage: /rollback filename")
        return True
    if cmd == "/version":
        say(version_text())
        return True
    if pending and not cmd:
        pending["chunks"].append((mid, text))
        return True
    return False
