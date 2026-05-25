import os
from datetime import time

# ── Fyers ──────────────────────────────────────────────────────────────────
FYERS_CLIENT_ID    = os.environ.get("FYERS_CLIENT_ID", "")
FYERS_ACCESS_TOKEN = os.environ.get("FYERS_ACCESS_TOKEN", "")

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Instrument ─────────────────────────────────────────────────────────────
INDEX_SYMBOL   = "NSE:NIFTY50-INDEX"
EXPIRY_DATE    = "26-05-2026"
EXPIRY_STR     = "26MAY"
STRIKE_STEP    = 100
STRADDLE_RANGE = 4

# ── Delta thresholds ───────────────────────────────────────────────────────
DELTA_ENTRY_MIN = 0.10
DELTA_ENTRY_MAX = 0.15
DELTA_ALERT     = 0.35
HEDGE_OFFSET    = 100

# ── Lot size ───────────────────────────────────────────────────────────────
LOT_SIZE    = 75
MAX_LOTS_CE = 10
MAX_LOTS_PE = 10

# ── Entry lots by score label: (ce_sell, pe_sell) ──────────────────────────
ENTRY_LOTS = {
    "very_bullish": (2, 4),
    "bullish":      (2, 4),
    "neutral":      (3, 3),
    "bearish":      (4, 2),
    "very_bearish": (4, 2),
}

# ── 12:00 addition lots by (entry_family, current_family): (ce_add, pe_add) ─
ADD_1200_LOTS = {
    ("bullish",  "very_bullish"): (0, 1),
    ("bullish",  "bullish"):      (0, 1),
    ("bullish",  "neutral"):      (2, 0),
    ("bullish",  "bearish"):      (4, 0),
    ("bullish",  "very_bearish"): (4, 0),
    ("bearish",  "very_bearish"): (1, 0),
    ("bearish",  "bearish"):      (1, 0),
    ("bearish",  "neutral"):      (0, 2),
    ("bearish",  "bullish"):      (0, 4),
    ("bearish",  "very_bullish"): (0, 4),
    ("neutral",  "neutral"):      (1, 1),
    ("neutral",  "bullish"):      (0, 3),
    ("neutral",  "very_bullish"): (0, 3),
    ("neutral",  "bearish"):      (3, 0),
    ("neutral",  "very_bearish"): (3, 0),
}

# ── Target ratio (ce, pe) for rebalancing ──────────────────────────────────
TARGET_RATIO = {
    "very_bullish": (2, 4),
    "bullish":      (2, 4),
    "neutral":      (4, 4),
    "bearish":      (4, 2),
    "very_bearish": (4, 2),
}

# ── Score labels ───────────────────────────────────────────────────────────
SCORE_LABELS = [
    ( 7.5,  "very_bullish"),
    ( 3.0,  "bullish"),
    (-2.5,  "neutral"),
    (-7.0,  "bearish"),
    (-11.5, "very_bearish"),
]

# ── Adjustment trigger thresholds (confirmed 2-bar score) ──────────────────
# Entry family → (trigger_if_score_below, trigger_if_score_above)
# None means that direction doesn't trigger
ADJ_TRIGGER = {
    "bullish":  (1.0,  None),   # adjust if score drops below +1.0
    "bearish":  (None, 0.0),    # adjust if score goes above 0.0
    "neutral":  (-3.0, 3.0),    # adjust if score exits ±3.0 band
}

# ── Max adjustments per day (includes 12pm add) ────────────────────────────
MAX_ADJUSTMENTS    = 4

# ── Post max-adj exit thresholds ──────────────────────────────────────────
POST_ADJ_EXIT_PNL  = 500       # close all if |pnl| > this after max adj
POST_ADJ_EXIT_LOSS = -500

# ── Trailing SL ────────────────────────────────────────────────────────────
TSL_ACTIVATE_PNL   = 1500      # TSL activates when peak >= this
TSL_INITIAL_GAP    = 500       # TSL = peak - 500 at activation
TSL_STEP_PROFIT    = 200       # every this much new profit above 1500
TSL_STEP_MOVE      = 100       # TSL moves up by this per step

# ── Timing ─────────────────────────────────────────────────────────────────
ENTRY_TIME    = time(10, 30)
ADD_1200_TIME = time(12,  0)
CLOSE_TIME    = time(15,  0)
MARKET_OPEN   = time( 9, 15)

# ── Candle interval ────────────────────────────────────────────────────────
INTERVAL = "5"

# ── File paths ─────────────────────────────────────────────────────────────
STATE_FILE     = "state.json"
DASHBOARD_FILE = "docs/index.html"

# ── Paper trading ──────────────────────────────────────────────────────────
PAPER_TRADE = True
