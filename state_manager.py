"""
state_manager.py
Persistent atomic state via JSON.
"""

import json, logging, os
from datetime import datetime
from config import STATE_FILE

log = logging.getLogger(__name__)

DEFAULT_STATE = {
    "date":               None,
    "atm":                None,
    # entry
    "entry_done":         False,
    "entry_label":        None,
    # view tracking
    "current_label":      None,
    "prev_label":         None,
    "pending_label":      None,
    "pending_label_count":0,
    "current_score":      None,
    "raw_label":          None,
    "prev_raw_label":     None,
    # adjustment tracking
    "adjustment_count":   0,
    "add_1200_done":      False,
    "neutral_adjusted":   False,
    # TSL
    "peak_pnl":           0.0,
    "tsl_level":          None,
    "tsl_alerted":        False,
    # positions
    "positions":          [],
    "delta_alerts_sent":  [],
    # close
    "closed":             False,
    "journal_saved":      False,
    # dashboard data
    "straddle_snapshot":  {},
    "view_log":           [],
    "equity_curve":       [],
    "last_run":           None,
    "errors":             []
}


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        log.info("No state file – starting fresh.")
        return dict(DEFAULT_STATE)
    try:
        with open(STATE_FILE) as f:
            data = json.load(f)
        for k, v in DEFAULT_STATE.items():
            if k not in data:
                data[k] = v
        return data
    except Exception as e:
        log.error(f"load_state error: {e} – fresh start.")
        return dict(DEFAULT_STATE)


def save_state(state: dict) -> bool:
    try:
        state["last_run"] = datetime.now().isoformat()
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2, default=str)
        os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:
        log.error(f"save_state error: {e}")
        return False


def reset_daily_state(state: dict, today: str) -> dict:
    fresh = dict(DEFAULT_STATE)
    fresh["date"]   = today
    fresh["errors"] = state.get("errors", [])
    return fresh


def append_error(state: dict, msg: str):
    state["errors"].append({"time": datetime.now().isoformat(), "msg": msg})
    state["errors"] = state["errors"][-10:]
