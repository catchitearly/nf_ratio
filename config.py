import os
from datetime import time

# ── Fyers ──────────────────────────────────────────────────────────────────
FYERS_CLIENT_ID   = os.environ.get("FYERS_CLIENT_ID", "")
FYERS_ACCESS_TOKEN = os.environ.get("FYERS_ACCESS_TOKEN", "")

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

# ── Instrument ─────────────────────────────────────────────────────────────
INDEX_SYMBOL   = "NSE:NIFTY50-INDEX"
EXPIRY_DATE    = "26-05-2026"          # current weekly expiry
EXPIRY_STR     = "26MAY"            # used in Fyers option symbol
STRIKE_STEP    = 100
STRADDLE_RANGE = 4                     # ATM ± 400  → 4 steps each side

# ── Delta thresholds ───────────────────────────────────────────────────────
DELTA_ENTRY_MIN  = 0.10
DELTA_ENTRY_MAX  = 0.15
DELTA_ALERT      = 0.35
HEDGE_OFFSET     = 100                 # points farther OTM from sold strike

# ── Lot size ───────────────────────────────────────────────────────────────
LOT_SIZE = 75                          # Nifty lot size

# ── Quantity (lots) ────────────────────────────────────────────────────────
QTY_NEUTRAL_CE   = 3
QTY_NEUTRAL_PE   = 3
QTY_BULL_CE      = 2
QTY_BULL_PE      = 4
QTY_BEAR_CE      = 4
QTY_BEAR_PE      = 2
QTY_ADD_CHANGE   = 3                   # lots added on confirmed view change
QTY_ADD_1135     = None                # filled dynamically from entry ratio

# ── Timing (IST, 24h) ─────────────────────────────────────────────────────
ENTRY_TIME          = time(10, 30)
VIEW_CHANGE_CONFIRM = 2                # consecutive 5-min bars
ADD_QTY_DEADLINE    = time(11, 35)     # one-time add if view unchanged
CLOSE_TIME          = time(15, 30)
MARKET_OPEN         = time(9, 15)

# ── Candle interval ────────────────────────────────────────────────────────
INTERVAL = "5"                         # minutes

# ── State file ─────────────────────────────────────────────────────────────
STATE_FILE     = "state.json"
DASHBOARD_FILE = "docs/index.html"

# ── Paper trading flag ─────────────────────────────────────────────────────
PAPER_TRADE = True
