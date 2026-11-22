"""The three gold setups. Each takes prepared candles and returns trade signals for the
test engine and for live alerts. These are first-guess settings: nothing was tuned on the data."""
import numpy as np
import pandas as pd
import config as cfg

def prepare(df):
    d = df.copy()
    pc = d.Close.shift()
    tr = pd.concat([d.High - d.Low, (d.High - pc).abs(), (d.Low - pc).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.rolling(14).mean()
    d["EMA20"] = d.Close.ewm(span=20, adjust=False).mean()
    d["EMA50"] = d.Close.ewm(span=50, adjust=False).mean()
    # 1-hour trend, using only finished hours
    h1 = d.Close.resample("1h").last().dropna()
    e20, e50 = h1.ewm(span=20, adjust=False).mean(), h1.ewm(span=50, adjust=False).mean()
    t = pd.Series(0, index=h1.index)
    t[(e20 > e50) & (h1 > e50)] = 1
    t[(e20 < e50) & (h1 < e50)] = -1
    d["TREND"] = t.shift(1).reindex(d.index, method="ffill").fillna(0)
    # VWAP resets at 00:00 UTC each day (only meaningful with real volume)
    day = d.index.normalize()
    typical = (d.High + d.Low + d.Close) / 3
    d["VWAP"] = (typical * d.Volume).groupby(day).cumsum() / d.Volume.groupby(day).cumsum().replace(0, np.nan)
    d["VOLAVG"] = d.Volume.rolling(20).mean().shift(1)
    return d

def _arr(d, *cols):
    return [d[c].to_numpy() for c in cols]

def _risk_ok(entry, stop, atr):
    return max(cfg.MIN_STOP, 0.5 * atr) <= abs(entry - stop) <= 3 * atr

def _sig(i, side, entry, stop, tp, name):
    return dict(i=i, side=side, entry=entry, stop=stop, tp=tp, setup=name)

def pullback(d, rr=None):
    """Trend pullback: 1h trend, price dips to the 5m EMA20 zone and holds above EMA50,
    entry on the bounce candle. Stop beyond the pullback, target rr x stop."""
    rr = rr or cfg.RR
    O, H, L, C, E20, E50, ATR, TR = _arr(d, "Open", "High", "Low", "Close", "EMA20", "EMA50", "ATR", "TREND")
    sigs, last = [], {"BUY": -99, "SELL": -99}
    for i in range(60, len(d)):
        a = ATR[i]
        if a != a or TR[i] == 0:
            continue
        if TR[i] == 1 and i - last["BUY"] > 12:
            if (np.min(L[i - 2:i + 1]) <= E20[i] and np.min(C[i - 2:i + 1]) > E50[i]
                    and C[i] > O[i] and C[i] > H[i - 1] and C[i] > E20[i]):
                entry, stop = C[i], np.min(L[i - 4:i + 1]) - 0.15 * a
                if _risk_ok(entry, stop, a):
                    sigs.append(_sig(i, "BUY", entry, stop, entry + rr * (entry - stop), "Trend pullback"))
                    last["BUY"] = i
        if TR[i] == -1 and i - last["SELL"] > 12:
            if (np.max(H[i - 2:i + 1]) >= E20[i] and np.max(C[i - 2:i + 1]) < E50[i]
                    and C[i] < O[i] and C[i] < L[i - 1] and C[i] < E20[i]):
                entry, stop = C[i], np.max(H[i - 4:i + 1]) + 0.15 * a
                if _risk_ok(entry, stop, a):
                    sigs.append(_sig(i, "SELL", entry, stop, entry - rr * (stop - entry), "Trend pullback"))
                    last["SELL"] = i
    return sigs

# (key, label, time zone, hour, minute)
SESSIONS = [("tokyo", "Tokyo 09:00", "Asia/Tokyo", 9, 0),
            ("london", "London 08:00", "Europe/London", 8, 0),
            ("ny", "New York 08:30", "America/New_York", 8, 30),
            ("us", "US stocks 09:30", "America/New_York", 9, 30)]

def breakout(d, rr=None):
    """Session-open breakout: range of the first 30 minutes after each session start; first
    strong close beyond the range within 2 hours; stop at mid-range. One attempt per session."""
    rr = rr or cfg.RR
    O, H, L, C, ATR, TR = _arr(d, "Open", "High", "Low", "Close", "ATR", "TREND")
    allowed = {x.strip().lower() for x in cfg.SESSIONS_ON.split(",")}
    sigs, n = [], len(d)
    for key, label, tz, hh, mm in SESSIONS:
        if "all" not in allowed and key not in allowed:
            continue
        loc = d.index.tz_convert(tz)  # handles summer/winter time automatically
        for s in np.where((loc.hour == hh) & (loc.minute == mm))[0]:
            e = s + 6
            if e >= n:
                continue
            rh, rl, a0 = H[s:e].max(), L[s:e].min(), ATR[e - 1]
            if a0 != a0 or not (0.8 * a0 <= rh - rl <= 4 * a0):
                continue
            for i in range(e, min(e + 24, n)):
                a = ATR[i]
                if a != a:
                    continue
                body = abs(C[i] - O[i])
                if C[i] > rh and C[i] > O[i] and body >= 0.6 * a:
                    side = "BUY"
                elif C[i] < rl and C[i] < O[i] and body >= 0.6 * a:
                    side = "SELL"
                else:
                    continue
                want = 1 if side == "BUY" else -1
                if (not cfg.BRK_TREND) or TR[i] == want:
                    entry, stop = C[i], (rh + rl) / 2
                    if _risk_ok(entry, stop, a):
                        tp = entry + want * rr * abs(entry - stop)
                        x = _sig(i, side, entry, stop, tp, f"Breakout {label}")
                        x["rng"] = (rh, rl)
                        sigs.append(x)
                break
    return sorted(sigs, key=lambda x: x["i"])

def vwap_snap(d, rr=None):
    """VWAP snap-back (needs volume, so it runs on futures): price stretched from VWAP,
    heavy-volume reversal candle with a long wick; target is VWAP. rr = minimum reward:risk."""
    min_rr = rr or 1.5
    O, H, L, C, V, VW, VA, ATR = _arr(d, "Open", "High", "Low", "Close", "Volume", "VWAP", "VOLAVG", "ATR")
    hours = d.index.hour.to_numpy()
    sigs, last = [], {"BUY": -99, "SELL": -99}
    for i in range(60, len(d)):
        a, v, va = ATR[i], VW[i], VA[i]
        if a != a or v != v or va != va or va <= 0 or not (6 <= hours[i] < 20):
            continue
        rng = H[i] - L[i]
        if rng <= 0 or V[i] < 1.5 * va:
            continue
        if (v - C[i] >= cfg.STRETCH * a and C[i] > O[i] and i - last["BUY"] > 12
                and (min(O[i], C[i]) - L[i]) >= 0.5 * rng):
            entry, stop = C[i], L[i] - 0.15 * a
            if _risk_ok(entry, stop, a) and (v - entry) >= min_rr * (entry - stop):
                sigs.append(_sig(i, "BUY", entry, stop, v, "VWAP snap-back"))
                last["BUY"] = i
        elif (C[i] - v >= cfg.STRETCH * a and C[i] < O[i] and i - last["SELL"] > 12
                and (H[i] - max(O[i], C[i])) >= 0.5 * rng):
            entry, stop = C[i], H[i] + 0.15 * a
            if _risk_ok(entry, stop, a) and (entry - v) >= min_rr * (stop - entry):
                sigs.append(_sig(i, "SELL", entry, stop, v, "VWAP snap-back"))
                last["SELL"] = i
    return sigs

# key: (name, which dataset it runs on, function)
SETUPS = {"pullback": ("Trend pullback", "spot", pullback),
          "breakout": ("Session-open breakout", "spot", breakout),
          "vwap": ("VWAP snap-back", "futures", vwap_snap)}
