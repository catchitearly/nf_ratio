"""
view_engine.py
Evaluates bullish / bearish / neutral view based on straddle VWAP conditions.

Bullish:
  - ATM straddle below VWAP
  - ATM+100 to ATM+400 : at least 3 below VWAP
  - ATM-400 to ATM-100 : at least 2 above VWAP

Bearish:
  - ATM+100 to ATM+400 : at least 3 above VWAP
  - ATM-400 to ATM-100 : at least 2 below VWAP

Neutral: neither condition met
"""

import logging
from fyers_data import get_straddle_candles
from vwap import straddle_vwap_status
from config import STRIKE_STEP, STRADDLE_RANGE

log = logging.getLogger(__name__)

VIEW_BULL    = "bullish"
VIEW_BEAR    = "bearish"
VIEW_NEUTRAL = "neutral"


def compute_view(atm: int) -> tuple[str, dict]:
    """
    Fetch all straddle data, compute VWAP status for each strike,
    derive directional view.

    Returns:
        (view_string, snapshot_dict)
        snapshot_dict has per-strike details for dashboard.
    """
    snapshot = {}
    try:
        strikes = [atm + i * STRIKE_STEP
                   for i in range(-STRADDLE_RANGE, STRADDLE_RANGE + 1)]  # ATM-400 to ATM+400

        for strike in strikes:
            candles = get_straddle_candles(strike)
            if candles:
                status = straddle_vwap_status(candles)
            else:
                status = {"vwap": None, "current_price": None,
                          "below_vwap": False, "above_vwap": False}
            snapshot[strike] = status

        # ── ATM check ────────────────────────────────────────────────────
        atm_status = snapshot.get(atm, {})
        atm_below  = atm_status.get("below_vwap", False)

        # ── Upper strikes: ATM+100 to ATM+400 ────────────────────────────
        upper = [atm + i * STRIKE_STEP for i in range(1, STRADDLE_RANGE + 1)]
        upper_below = sum(1 for s in upper if snapshot.get(s, {}).get("below_vwap", False))
        upper_above = sum(1 for s in upper if snapshot.get(s, {}).get("above_vwap", False))

        # ── Lower strikes: ATM-400 to ATM-100 ────────────────────────────
        lower = [atm - i * STRIKE_STEP for i in range(1, STRADDLE_RANGE + 1)]
        lower_above = sum(1 for s in lower if snapshot.get(s, {}).get("above_vwap", False))
        lower_below = sum(1 for s in lower if snapshot.get(s, {}).get("below_vwap", False))

        log.info(
            f"View check | ATM below_vwap={atm_below} | "
            f"upper_below={upper_below} upper_above={upper_above} | "
            f"lower_above={lower_above} lower_below={lower_below}"
        )

        # ── View determination ────────────────────────────────────────────
        if atm_below and upper_below >= 3 and lower_above >= 2:
            view = VIEW_BULL
        elif upper_above >= 3 and lower_below >= 2:
            view = VIEW_BEAR
        else:
            view = VIEW_NEUTRAL

        log.info(f"Computed view: {view}")
        return view, snapshot

    except Exception as e:
        log.error(f"compute_view error: {e}")
        return VIEW_NEUTRAL, snapshot


def update_view_state(state: dict, new_view: str) -> tuple[str | None, bool]:
    """
    Implements 2-consecutive-bar confirmation logic.

    Returns:
        (confirmed_view_or_None, view_changed_bool)
        confirmed_view is None if not yet confirmed.
    """
    try:
        current = state.get("current_view")

        if new_view == current:
            # View unchanged – reset pending
            state["pending_view"]       = None
            state["pending_view_count"] = 0
            return current, False

        # View differs from confirmed view
        if state.get("pending_view") == new_view:
            state["pending_view_count"] = state.get("pending_view_count", 0) + 1
        else:
            state["pending_view"]       = new_view
            state["pending_view_count"] = 1

        if state["pending_view_count"] >= 2:
            # Confirmed change
            old_view = state["current_view"]
            state["current_view"]       = new_view
            state["prev_view"]          = old_view
            state["pending_view"]       = None
            state["pending_view_count"] = 0
            log.info(f"View confirmed changed: {old_view} → {new_view}")
            return new_view, True

        log.info(
            f"View pending ({state['pending_view_count']}/2): "
            f"current={current} pending={new_view}"
        )
        return None, False

    except Exception as e:
        log.error(f"update_view_state error: {e}")
        return None, False
