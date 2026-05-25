"""
view_engine.py
Computes directional score (-11.5 to +11.5) and label from straddle VWAP data.

Scoring weights:
  Upper zone (ATM+100 to ATM+400): below VWAP = bullish
    ATM+100: ±1.0, ATM+200: ±1.0, ATM+300: ±1.5, ATM+400: ±2.0
  ATM: below VWAP = +0.5, above = -0.5
  Lower zone (ATM-100 to ATM-400): INVERTED — above VWAP = bullish
    ATM-100: ±1.0, ATM-200: ±1.0, ATM-300: ±1.5, ATM-400: ±2.0

Labels:
  >= +7.5  → very_bullish
  >= +3.0  → bullish
  >= -2.5  → neutral
  >= -7.0  → bearish
  else     → very_bearish
"""

import logging
from fyers_data import get_straddle_candles
from vwap import straddle_vwap_status
from config import STRIKE_STEP, STRADDLE_RANGE, SCORE_LABELS

log = logging.getLogger(__name__)

# Zone weights by distance from ATM (steps 1-4)
ZONE_WEIGHTS = {1: 1.0, 2: 1.0, 3: 1.5, 4: 2.0}
ATM_WEIGHT   = 0.5


def compute_score(atm_below: bool,
                  upper_below: int, upper_above: int,
                  lower_above: int, lower_below: int) -> float:
    """
    Compute directional score from VWAP counts.
    Upper zone: below=bullish. Lower zone: above=bullish (inverted).
    Weights assigned greedily highest→lowest to whichever direction has more strikes.
    """
    weights = sorted(ZONE_WEIGHTS.values(), reverse=True)  # [2.0, 1.5, 1.0, 1.0]

    # ATM
    score = ATM_WEIGHT if atm_below else -ATM_WEIGHT

    # Upper zone
    ub, ua = upper_below, upper_above
    for w in weights:
        if ub > 0:
            score += w; ub -= 1
        elif ua > 0:
            score -= w; ua -= 1

    # Lower zone (inverted)
    la, lb = lower_above, lower_below
    for w in weights:
        if la > 0:
            score += w; la -= 1
        elif lb > 0:
            score -= w; lb -= 1

    return round(score, 1)


def score_to_label(score: float) -> str:
    for threshold, label in SCORE_LABELS:
        if score >= threshold:
            return label
    return "very_bearish"


def label_to_direction(label: str) -> str:
    """Map 5-level label to 3-level direction for position logic."""
    if label in ("very_bullish", "bullish"):
        return "bullish"
    if label in ("very_bearish", "bearish"):
        return "bearish"
    return "neutral"


def compute_view(atm: int) -> tuple[str, float, str, dict]:
    """
    Fetch all straddle data, compute score and label.

    Returns:
        (label, score, direction, snapshot)
        label:     very_bullish / bullish / neutral / bearish / very_bearish
        score:     float -11.5 to +11.5
        direction: bullish / neutral / bearish  (for position sizing)
        snapshot:  per-strike VWAP details for dashboard
    """
    snapshot = {}
    try:
        strikes = [atm + i * STRIKE_STEP
                   for i in range(-STRADDLE_RANGE, STRADDLE_RANGE + 1)]

        for strike in strikes:
            candles = get_straddle_candles(strike)
            if candles:
                status = straddle_vwap_status(candles)
            else:
                status = {"vwap": None, "current_price": None,
                          "below_vwap": False, "above_vwap": False}
            snapshot[strike] = status

        atm_below   = snapshot.get(atm, {}).get("below_vwap", False)
        upper       = [atm + i * STRIKE_STEP for i in range(1, STRADDLE_RANGE + 1)]
        lower       = [atm - i * STRIKE_STEP for i in range(1, STRADDLE_RANGE + 1)]
        upper_below = sum(1 for s in upper if snapshot.get(s, {}).get("below_vwap"))
        upper_above = sum(1 for s in upper if snapshot.get(s, {}).get("above_vwap"))
        lower_above = sum(1 for s in lower if snapshot.get(s, {}).get("above_vwap"))
        lower_below = sum(1 for s in lower if snapshot.get(s, {}).get("below_vwap"))

        score     = compute_score(atm_below, upper_below, upper_above,
                                  lower_above, lower_below)
        label     = score_to_label(score)
        direction = label_to_direction(label)

        log.info(
            f"View check | ATM below_vwap={atm_below} | "
            f"upper_below={upper_below} upper_above={upper_above} | "
            f"lower_above={lower_above} lower_below={lower_below} | "
            f"score={score:+.1f} label={label}"
        )
        return label, score, direction, snapshot

    except Exception as e:
        log.error(f"compute_view error: {e}")
        return "neutral", 0.0, "neutral", snapshot


def update_view_state(state: dict, new_label: str) -> tuple[str | None, bool]:
    """
    2-consecutive-bar confirmation for label changes.
    Returns (confirmed_label_or_None, changed_bool).
    """
    try:
        current = state.get("current_label")

        if new_label == current:
            state["pending_label"]       = None
            state["pending_label_count"] = 0
            return current, False

        if state.get("pending_label") == new_label:
            state["pending_label_count"] = state.get("pending_label_count", 0) + 1
        else:
            state["pending_label"]       = new_label
            state["pending_label_count"] = 1

        if state["pending_label_count"] >= 2:
            old = state.get("current_label")
            state["current_label"]       = new_label
            state["prev_label"]          = old
            state["pending_label"]       = None
            state["pending_label_count"] = 0
            log.info(f"Label confirmed: {old} → {new_label}")
            return new_label, True

        log.info(f"Label pending ({state['pending_label_count']}/2): "
                 f"current={current} pending={new_label}")
        return None, False

    except Exception as e:
        log.error(f"update_view_state error: {e}")
        return None, False
