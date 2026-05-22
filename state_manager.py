"""
state_manager.py
Handles reading and writing the persistent state.json file.
Every key piece of runtime data lives here so a crash mid-run
is fully recoverable on the next 5-min cron tick.
"""

import json
import logging
import os
from datetime import datetime
from config import STATE_FILE

log = logging.getLogger(__name__)

DEFAULT_STATE = {
    "date": None,                  # trading date (YYYY-MM-DD)
    "atm": None,                   # ATM strike fixed at 9:15
    "entry_done": False,           # True once 10:30 entry executed
    "entry_view": None,            # view at entry: bull/bear/neutral
    "current_view": None,          # latest confirmed view
    "prev_view": None,             # view from previous 5-min tick
    "pending_view": None,          # unconfirmed view (1st consecutive)
    "pending_view_count": 0,       # how many consecutive bars this pending view held
    "add_1135_done": False,        # one-time 11:35 qty addition flag
    "closed": False,               # True after 3pm flat close
    "positions": [],               # list of open leg dicts
    "trade_log": [],               # all executed (paper) trades
    "delta_alerts_sent": [],       # symbols already alerted for delta breach
    "straddle_vwap_history": [],   # last N straddle snapshots for dashboard
    "view_log": [],                # [{time, view, pending, spot}] every 5-min tick
    "equity_curve": [],            # [{time, pnl}] every 5-min tick after entry
    "last_run": None,              # ISO timestamp of last successful run
    "errors": []                   # last 10 errors for dashboard display
}


def load_state() -> dict:
    """Load state from disk; return default if missing or corrupt."""
    if not os.path.exists(STATE_FILE):
        log.info("No state file found – starting fresh.")
        return dict(DEFAULT_STATE)
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
        # Merge any missing keys from DEFAULT_STATE (handles schema upgrades)
        for k, v in DEFAULT_STATE.items():
            if k not in data:
                data[k] = v
        return data
    except Exception as e:
        log.error(f"Failed to load state: {e} – starting fresh.")
        return dict(DEFAULT_STATE)


def save_state(state: dict) -> bool:
    """Atomically write state to disk."""
    try:
        state["last_run"] = datetime.now().isoformat()
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:
        log.error(f"Failed to save state: {e}")
        return False


def reset_daily_state(state: dict, today: str) -> dict:
    """Reset trading state for a new day, preserve error log."""
    log.info(f"Resetting state for new day: {today}")
    fresh = dict(DEFAULT_STATE)
    fresh["date"] = today
    fresh["errors"] = state.get("errors", [])
    return fresh


def append_error(state: dict, msg: str):
    """Keep last 10 errors in state for dashboard."""
    state["errors"].append({"time": datetime.now().isoformat(), "msg": msg})
    state["errors"] = state["errors"][-10:]
