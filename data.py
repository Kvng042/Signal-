"""Downloads and saves gold prices (5-minute candles, all times UTC).
  spot    = XAU/USD from Twelve Data: long history, no volume
  futures = GC=F from Yahoo: about 59 days, with real volume
"""
import os, time
import pandas as pd
import requests
import yfinance as yf
import config as cfg

COLS = ["Open", "High", "Low", "Close", "Volume"]

def _path(name):
    os.makedirs(cfg.DATA_DIR, exist_ok=True)
    return os.path.join(cfg.DATA_DIR, f"{name}_5m.csv")

def read(name):
    p = _path(name)
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)
    return df

def _closed_only(df):
    return df[df.index + pd.Timedelta(minutes=5) <= pd.Timestamp.now(tz="UTC")]

def load_futures():
    df = yf.Ticker("GC=F").history(period="59d", interval="5m")
    if df.empty:
        return read("futures"), "Yahoo returned no data"
    df = df[COLS].copy()
    df.index = df.index.tz_convert("UTC")
    df = _closed_only(df)
    df.to_csv(_path("futures"))
    return df, "ok"

def _twelve_page(end_dt):
    r = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
        "symbol": "XAU/USD", "interval": "5min", "outputsize": 5000, "timezone": "UTC",
        "end_date": end_dt.strftime("%Y-%m-%d %H:%M:%S"), "apikey": cfg.TWELVE_KEY})
    return r.json()

def load_spot():
    old = read("spot")
    if not cfg.TWELVE_KEY:
        return old, "TWELVE_KEY is not set"
    target = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=cfg.HISTORY_DAYS)
    covered = old is not None and len(old) > 0 and old.index[0] <= target + pd.Timedelta(days=2)
    stop_at = old.index[-1] if covered else target
    cursor, frames, pages, retries = pd.Timestamp.now(tz="UTC"), [], 0, 0
    while pages < 40:
        try:
            res = _twelve_page(cursor)
        except Exception as e:
            if frames:
                break
            return old, f"request failed: {str(e)[:80]}"
        if "values" not in res:
            if res.get("code") == 429 and retries < 3:
                retries += 1
                time.sleep(62)  # free plan: 8 requests per minute
                continue
            if frames:
                break
            return old, f"Twelve Data error: {str(res.get('message', 'unknown'))[:120]}"
        df = pd.DataFrame(res["values"])
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").sort_index()
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype(float)
        df = df.rename(columns=str.capitalize)[["Open", "High", "Low", "Close"]]
        df["Volume"] = 0.0
        frames.append(df)
        pages += 1
        earliest = df.index[0]
        if earliest <= stop_at or len(df) < 5000:
            break
        cursor = earliest - pd.Timedelta(minutes=5)
        time.sleep(8)
    if not frames:
        return old, "Twelve Data returned no candles"
    parts = ([old] if old is not None else []) + frames
    out = pd.concat(parts)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = _closed_only(out[out.index >= target])
    out.to_csv(_path("spot"))
    return out, f"ok, {pages} request(s)"

def describe(df, label):
    if df is None or df.empty:
        return f"{label}: no data"
    days = (df.index[-1] - df.index[0]).days
    return (f"{label}: {len(df):,} candles, {df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d} "
            f"({days} days), last price {df.Close.iloc[-1]:.2f}")

def basis(spot, fut):
    if spot is None or fut is None:
        return None
    common = spot.index.intersection(fut.index)
    if len(common) == 0:
        return None
    t = common[-1]
    return float(fut.Close[t] - spot.Close[t])
