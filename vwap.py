"""
vwap.py
Computes VWAP for a list of OHLC+volume candle dicts.
Uses typical price = (high + low + close) / 3.
Resets each day (all candles passed are assumed same session).
"""

import logging

log = logging.getLogger(__name__)


def compute_vwap(candles: list[dict]) -> float | None:
    """
    Compute session VWAP from candle list.
    Each candle: {ts, open, high, low, close, volume}
    Returns None if insufficient data.
    """
    try:
        if not candles:
            return None

        cum_pv = 0.0
        cum_v  = 0.0

        for c in candles:
            typical = (c["high"] + c["low"] + c["close"]) / 3
            vol = c["volume"] if c["volume"] > 0 else 1  # avoid zero-vol edge case
            cum_pv += typical * vol
            cum_v  += vol

        if cum_v == 0:
            return None

        return cum_pv / cum_v
    except Exception as e:
        log.error(f"compute_vwap error: {e}")
        return None


def get_current_straddle_price(candles: list[dict]) -> float | None:
    """Return last candle's close (current straddle price)."""
    try:
        if not candles:
            return None
        return candles[-1]["close"]
    except Exception as e:
        log.error(f"get_current_straddle_price error: {e}")
        return None


def straddle_vwap_status(candles: list[dict]) -> dict:
    """
    Returns:
      {
        vwap: float,
        current_price: float,
        below_vwap: bool,
        above_vwap: bool,
      }
    """
    result = {"vwap": None, "current_price": None,
              "below_vwap": False, "above_vwap": False}
    try:
        vwap  = compute_vwap(candles)
        price = get_current_straddle_price(candles)

        if vwap is None or price is None:
            return result

        result["vwap"]          = round(vwap, 2)
        result["current_price"] = round(price, 2)
        result["below_vwap"]    = price < vwap
        result["above_vwap"]    = price > vwap
    except Exception as e:
        log.error(f"straddle_vwap_status error: {e}")
    return result
