"""
fyers_data.py
Fetches OHLC candle data and option quotes from Fyers API.
Uses fyers_apiv3 SDK (fyersModel) — same approach as confirmed working code.
All calls wrapped in try/except; returns None on failure.
"""

import logging
import math
from datetime import datetime, date, timedelta

from fyers_apiv3 import fyersModel

from config import (
    FYERS_CLIENT_ID, FYERS_ACCESS_TOKEN,
    INDEX_SYMBOL, EXPIRY_STR, INTERVAL, STRIKE_STEP
)

log = logging.getLogger(__name__)

# ── Singleton fyers client ─────────────────────────────────────────────────
_fyers: fyersModel.FyersModel | None = None

def get_fyers() -> fyersModel.FyersModel | None:
    """Return authenticated FyersModel instance, initialised once per run."""
    global _fyers
    if _fyers is not None:
        return _fyers
    try:
        if not FYERS_CLIENT_ID or not FYERS_ACCESS_TOKEN:
            log.error("FYERS_CLIENT_ID or FYERS_ACCESS_TOKEN not set.")
            return None
        client = fyersModel.FyersModel(
            client_id=FYERS_CLIENT_ID,
            token=FYERS_ACCESS_TOKEN,
            log_path=""          # suppress SDK file logs
        )
        # Quick auth check
        profile = client.get_profile()
        if profile.get("s") != "ok":
            log.error(f"Fyers auth failed: {profile}")
            return None
        log.info("Fyers authenticated successfully.")
        _fyers = client
        return _fyers
    except Exception as e:
        log.error(f"get_fyers error: {e}")
        return None


# ── Helpers ────────────────────────────────────────────────────────────────

def _option_symbol(strike: int, opt_type: str) -> str:
    """Build Fyers option symbol e.g. NSE:NIFTY26MAY2623700CE"""
    return f"NSE:NIFTY{EXPIRY_STR}{strike}{opt_type}"


def _fetch_candles_raw(symbol: str) -> list | None:
    """
    Core candle fetcher using fyers SDK .history() — same as working code.
    Returns raw list of [epoch, open, high, low, close, volume] or None.
    """
    try:
        fyers = get_fyers()
        if fyers is None:
            return None

        today    = date.today().strftime("%Y-%m-%d")
        payload  = {
            "symbol":      symbol,
            "resolution":  INTERVAL,
            "date_format": "1",        # epoch timestamps
            "range_from":  today,
            "range_to":    today,
            "cont_flag":   "1"
        }
        resp = fyers.history(data=payload)

        if resp.get("s") != "ok" or not resp.get("candles"):
            log.warning(f"No candles for {symbol}: {resp.get('message','')}")
            return None

        return resp["candles"]
    except Exception as e:
        log.error(f"_fetch_candles_raw error ({symbol}): {e}")
        return None


# ── Public API ─────────────────────────────────────────────────────────────

def get_spot_price() -> float | None:
    """Fetch latest Nifty spot LTP via quotes."""
    try:
        fyers = get_fyers()
        if fyers is None:
            return None

        resp = fyers.quotes(data={"symbols": INDEX_SYMBOL})

        if resp.get("s") != "ok":
            log.error(f"get_spot_price error: {resp}")
            return None

        ltp = resp["d"][0]["v"]["lp"]
        log.info(f"Spot price: {ltp}")
        return float(ltp)
    except Exception as e:
        log.error(f"get_spot_price error: {e}")
        return None


def get_915_candle_close() -> float | None:
    """
    Return 9:15 candle close for Nifty index (first candle of the day).
    Used to fix ATM when script starts after 9:15.
    """
    try:
        candles = _fetch_candles_raw(INDEX_SYMBOL)
        if not candles:
            return None
        close = float(candles[0][4])   # first candle, close = index 4
        log.info(f"9:15 candle close: {close}")
        return close
    except Exception as e:
        log.error(f"get_915_candle_close error: {e}")
        return None


def get_candles(symbol: str) -> list[dict] | None:
    """
    Fetch today's 5-min OHLC candles for any symbol.
    Returns list of {ts, open, high, low, close, volume} or None.
    """
    try:
        raw = _fetch_candles_raw(symbol)
        if not raw:
            return None
        return [
            {"ts": c[0], "open": c[1], "high": c[2],
             "low": c[3], "close": c[4], "volume": c[5]}
            for c in raw
        ]
    except Exception as e:
        log.error(f"get_candles error ({symbol}): {e}")
        return None


def get_option_quote(strike: int, opt_type: str) -> dict | None:
    """
    Fetch latest quote for one option leg.
    Returns dict with ltp, delta (if available) or None.
    """
    try:
        fyers  = get_fyers()
        if fyers is None:
            return None

        symbol = _option_symbol(strike, opt_type)
        resp   = fyers.quotes(data={"symbols": symbol})

        if resp.get("s") != "ok" or not resp.get("d"):
            log.warning(f"get_option_quote no data ({symbol}): {resp.get('message','')}")
            return None

        v = resp["d"][0]["v"]
        result = {
            "symbol":   symbol,
            "strike":   strike,
            "opt_type": opt_type,
            "ltp":      float(v.get("lp", 0)),
            "bid":      float(v.get("bid_price", 0)),
            "ask":      float(v.get("ask_price", 0)),
            "volume":   int(v.get("volume", 0)),
        }

        greeks = v.get("greeks", {})
        if greeks and greeks.get("delta") is not None:
            result["delta"]         = abs(float(greeks["delta"]))
            result["greeks_source"] = "fyers"
        else:
            result["delta"]         = None
            result["greeks_source"] = "bs"

        return result
    except Exception as e:
        log.error(f"get_option_quote error ({strike}{opt_type}): {e}")
        return None


def get_straddle_candles(strike: int) -> list[dict] | None:
    """
    Fetch CE+PE 5-min candles for a strike and return combined straddle candles.
    Aligns by timestamp (same logic as working code's pd.merge on 'time').
    Returns list of {ts, open, high, low, close, volume} or None.
    """
    try:
        ce_sym = _option_symbol(strike, "CE")
        pe_sym = _option_symbol(strike, "PE")

        ce_raw = _fetch_candles_raw(ce_sym)
        pe_raw = _fetch_candles_raw(pe_sym)

        if not ce_raw or not pe_raw:
            log.warning(f"get_straddle_candles: missing data for strike {strike}")
            return None

        # Build PE lookup by epoch timestamp
        pe_map = {c[0]: c for c in pe_raw}

        combined = []
        for c in ce_raw:
            ts = c[0]
            if ts in pe_map:
                pe = pe_map[ts]
                combined.append({
                    "ts":     ts,
                    "open":   c[1] + pe[1],
                    "high":   c[2] + pe[2],
                    "low":    c[3] + pe[3],
                    "close":  c[4] + pe[4],
                    "volume": c[5] + pe[5]
                })

        if not combined:
            log.warning(f"get_straddle_candles: no aligned candles for strike {strike}")
            return None

        log.info(f"Straddle candles fetched for {strike}: {len(combined)} bars")
        return combined
    except Exception as e:
        log.error(f"get_straddle_candles error (strike={strike}): {e}")
        return None


# ── Black-Scholes delta fallback ───────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def bs_delta(spot: float, strike: float, opt_type: str,
             days_to_expiry: float, iv: float = 0.15, r: float = 0.065) -> float:
    """Black-Scholes delta. Returns abs value for PE."""
    try:
        T  = max(days_to_expiry / 365, 1e-6)
        d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
        if opt_type == "CE":
            return _norm_cdf(d1)
        else:
            return abs(_norm_cdf(d1) - 1)
    except Exception as e:
        log.error(f"bs_delta error: {e}")
        return 0.0
