"""The test engine. Give it candles and trade signals; it replays every trade with the
spread deducted and reports the results, split into an older and a newest period."""
import numpy as np
import pandas as pd
import config as cfg

def add_atr(df, n=14):
    pc = df.Close.shift()
    tr = pd.concat([df.High - df.Low, (df.High - pc).abs(), (df.Low - pc).abs()], axis=1).max(axis=1)
    out = df.copy()
    out["ATR"] = tr.rolling(n).mean()
    return out

def simulate(df, signals):
    """signals: dicts with i (candle position where the trade is entered at the close),
    side, entry, stop, tp. A candle that touches both stop and target counts as a loss."""
    H, L, Cl = df.High.to_numpy(), df.Low.to_numpy(), df.Close.to_numpy()
    idx, n, rows = df.index, len(df), []
    for s in signals:
        i, side, entry, stop, tp = s["i"], s["side"], s["entry"], s["stop"], s["tp"]
        risk = abs(entry - stop)
        if risk <= 0 or i + 1 >= n:
            continue
        end = min(n, i + 1 + cfg.HORIZON)
        outcome, exit_price = None, None
        for j in range(i + 1, end):
            if side == "BUY":
                if L[j] <= stop:
                    outcome, exit_price = "LOSS", stop
                    break
                if H[j] >= tp:
                    outcome, exit_price = "WIN", tp
                    break
            else:
                if H[j] >= stop:
                    outcome, exit_price = "LOSS", stop
                    break
                if L[j] <= tp:
                    outcome, exit_price = "WIN", tp
                    break
        if outcome is None:
            if end - (i + 1) < cfg.HORIZON:
                continue  # trade has not had time to finish yet
            outcome, exit_price = "TIMEOUT", Cl[end - 1]
        gross = (exit_price - entry) / risk if side == "BUY" else (entry - exit_price) / risk
        rows.append(dict(time=idx[i], side=side, setup=s.get("setup", ""),
                         outcome=outcome, R=gross - cfg.SPREAD / risk))
    return pd.DataFrame(rows)

def summarize(t):
    if t is None or t.empty:
        return {"n": 0}
    r = t.R.to_numpy()
    daily = t.groupby(t.time.dt.date).R.sum()
    streak = worst = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        worst = max(worst, streak)
    gain, loss = r[r > 0].sum(), -r[r <= 0].sum()
    return dict(n=len(r), win=100 * (r > 0).mean(), avg_r=r.mean(), total_r=r.sum(),
                pf=min(gain / loss, 99) if loss > 0 else 99, green_days=100 * (daily > 0).mean(),
                per_day=len(r) / len(daily), worst_streak=worst)

def _line(name, s):
    if not s.get("n"):
        return f"{name}: no trades"
    return (f"{name}: {s['n']} trades, win {s['win']:.0f}%, avg {s['avg_r']:+.2f}R, "
            f"profit factor {s['pf']:.2f}, profitable days {s['green_days']:.0f}%, "
            f"worst losing streak {s['worst_streak']}, {s['per_day']:.1f} trades/day")

def passes(trades):
    if trades is None or trades.empty:
        return False
    t = trades.sort_values("time").reset_index(drop=True)
    cut = int(len(t) * (1 - cfg.TEST_FRACTION))
    a, b = summarize(t.iloc[:cut]), summarize(t.iloc[cut:])
    return (a.get("n", 0) >= cfg.MIN_TRADES and b.get("n", 0) >= cfg.MIN_TRADES
            and a["avg_r"] > 0 and b["avg_r"] > 0)

def report(title, trades):
    if trades is None or trades.empty:
        return f"{title}\nNo trades."
    t = trades.sort_values("time").reset_index(drop=True)
    cut = int(len(t) * (1 - cfg.TEST_FRACTION))
    train, test = summarize(t.iloc[:cut]), summarize(t.iloc[cut:])
    return "\n".join([
        title,
        f"Period: {t.time.iloc[0]:%Y-%m-%d} to {t.time.iloc[-1]:%Y-%m-%d}, spread ${cfg.SPREAD:g} deducted",
        _line("Older period", train),
        _line("Newest period", test),
        "Verdict: " + ("PASS" if passes(trades) else "FAIL")
        + f" (needs {cfg.MIN_TRADES}+ trades and positive average R in both periods)"])

def breakdown(trades):
    """Results per setup label (for example per session)."""
    if trades is None or trades.empty or trades.setup.nunique() < 2:
        return ""
    lines = ["By session:"]
    for name, g in trades.groupby("setup"):
        s = summarize(g)
        lines.append(f"{name}: {s['n']} trades, win {s['win']:.0f}%, avg {s['avg_r']:+.2f}R")
    return "\n".join(lines)

def baseline_signals(df, times=((8, 0), (13, 30), (15, 0)), rr=2.0):
    """Benchmark: buy AND sell at fixed times, stop 1 ATR, target 2 ATR."""
    atr, close = df.ATR.to_numpy(), df.Close.to_numpy()
    mask = np.zeros(len(df), dtype=bool)
    for h, m in times:
        mask |= (df.index.hour == h) & (df.index.minute == m)
    sigs = []
    for i in np.where(mask)[0]:
        a = atr[i]
        if a != a or a <= 0:
            continue
        e = close[i]
        sigs.append(dict(i=i, side="BUY", entry=e, stop=e - a, tp=e + rr * a, setup="baseline"))
        sigs.append(dict(i=i, side="SELL", entry=e, stop=e + a, tp=e - rr * a, setup="baseline"))
    return sigs
