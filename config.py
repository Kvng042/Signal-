"""All settings live here. Every value can be changed with an environment variable."""
import os

def env(name, default):
    return type(default)(os.environ.get(name, default))

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
SCAN_KEY = os.environ.get("SCAN_KEY", "")
TWELVE_KEY = os.environ.get("TWELVE_KEY", "")

HISTORY_DAYS = env("HISTORY_DAYS", 365)    # how far back to download spot gold
SPREAD = env("SPREAD", 0.25)               # gold spread in dollars, deducted from every trade
HORIZON = env("HORIZON", 96)               # max candles a trade stays open (96 x 5min = 8 hours)
TEST_FRACTION = env("TEST_FRACTION", 0.3)  # newest 30% is kept for confirmation
MIN_TRADES = env("MIN_TRADES", 20)         # minimum trades per period to call a setup proven
DATA_DIR = os.environ.get("DATA_DIR", "data")

RR = env("RR", 2.0)               # target distance, in multiples of the stop distance
MIN_STOP = env("MIN_STOP", 2.0)   # smallest allowed stop, in dollars
STRETCH = env("STRETCH", 2.0)     # VWAP snap-back: distance from VWAP, in ATRs
BRK_TREND = env("BRK_TREND", 1)   # breakout: 1 = trade only with the 1h trend
TRAIL = env("TRAIL", 1.0)         # trailing stop distance behind the best price, in R
SESSIONS_ON = env("SESSIONS_ON", "all")  # breakout sessions: all, or a list like "ny,us"
                                         # (keys: tokyo, london, ny, us)
