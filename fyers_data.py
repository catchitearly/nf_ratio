"""
fyers_data.py
Fetches OHLC candle data and option greeks from Fyers REST API.
All calls are wrapped in try/except; returns None on failure.
"""

import logging
import math
import time as time_mod
from datetime import datetime, date, timedelta
import requests
from fyers_apiv3 import fyersModel


from config import (
    FYERS_CLIENT_ID, FYERS_ACCESS_TOKEN,
    INDEX_SYMBOL, EXPIRY_STR, INTERVAL, STRIKE_STEP
)

log = logging.getLogger(__name__)

BASE_URL   = "https://api.fyers.in/data-rest/v2"
HEADERS    = {"Content-Type": "application/json"}

# ── Helpers ────────────────────────────────────────────────────────────────

def _auth_header() -> dict:
    return {"Authorization": f"{FYERS_CLIENT_ID}:{FYERS_ACCESS_TOKEN}"}


def _option_symbol(strike: int, opt_type: str) -> str:
    """Build Fyers option symbol e.g. NSE:NIFTY2552623700CE"""
    # Format: NSE:NIFTY{YY}{MMM}{STRIKE}{CE/PE}
    return f"NSE:NIFTY{EXPIRY_STR}{strike}{opt_type}"


def get_915_candle_close() -> float | None:
    """
    Fetch the 9:15 AM candle close price for Nifty index today.
    Used to fix ATM correctly even when script starts after 9:15.
    Returns None if candle not available yet or on error.
    """
    try:
        today     = date.today()
        date_str  = today.strftime("%Y-%m-%d")
        url       = f"{BASE_URL}/history/"
        payload   = {
            "symbol":      INDEX_SYMBOL,
            "resolution":  "5",
            "date_format": "1",
            "range_from":  date_str,
            "range_to":    date_str,
            "cont_flag":   "1"
        }
        r = requests.get(url, headers={**HEADERS, **_auth_header()},
                         params=payload, timeout=10)
        r.raise_for_status()
        data = r.json()

        if data.get("s") != "ok" or not data.get("candles"):
            log.warning("get_915_candle_close: no candles returned")
            return None

        # First candle of the day = 9:15 candle; close is index 4
        first_candle = data["candles"][0]
        close_price  = float(first_candle[4])
        log.info(f"9:15 candle close fetched: {close_price}")
        return close_price
    except Exception as e:
        log.error(f"get_915_candle_close error: {e}")
        return None


def get_spot_price() -> float | None:
    """Fetch latest Nifty spot price."""
    try:
        url = f"{BASE_URL}/quotes/"
        params = {"symbols": INDEX_SYMBOL}
        r = requests.get(url, headers={**HEADERS, **_auth_header()},
                         params=params, timeout=10)
        if not r.ok:
            log.error(f"get_spot_price HTTP {r.status_code}: {r.text[:300]}")
            return None
        data = r.json()
        if data.get("s") != "ok":
            log.error(f"get_spot_price API error: {data}")
            return None
        ltp = data["d"][0]["v"]["lp"]
        return float(ltp)
    except Exception as e:
        log.error(f"get_spot_price error: {e}")
        return None


def get_candles(symbol: str, days_back: int = 1) -> list[dict] | None:
    """
    Fetch 5-min OHLC candles for `symbol` from today's open.
    Returns list of {ts, open, high, low, close, volume} dicts.
    """
    try:
        today = date.today()
        date_from = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        date_to   = today.strftime("%Y-%m-%d")

        url = f"{BASE_URL}/history/"
        payload = {
            "symbol":      symbol,
            "resolution":  INTERVAL,
            "date_format": "1",          # epoch timestamps
            "range_from":  date_from,
            "range_to":    date_to,
            "cont_flag":   "1"
        }
        r = requests.get(url, headers={**HEADERS, **_auth_header()},
                         params=payload, timeout=15)
        r.raise_for_status()
        data = r.json()

        if data.get("s") != "ok":
            log.warning(f"Fyers candles bad response for {symbol}: {data.get('s')}")
            return None

        candles = []
        for c in data["candles"]:
            candles.append({
                "ts": c[0], "open": c[1], "high": c[2],
                "low": c[3], "close": c[4], "volume": c[5]
            })
        return candles
    except Exception as e:
        log.error(f"get_candles error ({symbol}): {e}")
        return None


def get_option_quote(strike: int, opt_type: str) -> dict | None:
    """
    Fetch latest quote for one option leg.
    Returns dict with ltp, greeks (delta etc.) if available.
    """
    try:
        symbol = _option_symbol(strike, opt_type)
        url = f"{BASE_URL}/quotes/"
        params = {"symbols": symbol}
        r = requests.get(url, headers={**HEADERS, **_auth_header()},
                         params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        if not data.get("d"):
            return None

        v = data["d"][0]["v"]
        result = {
            "symbol":  symbol,
            "strike":  strike,
            "opt_type": opt_type,
            "ltp":     float(v.get("lp", 0)),
            "bid":     float(v.get("bid_price", 0)),
            "ask":     float(v.get("ask_price", 0)),
            "volume":  int(v.get("volume", 0)),
        }

        # Fyers sometimes returns greeks in the quote
        greeks = v.get("greeks", {})
        if greeks and greeks.get("delta") is not None:
            result["delta"] = abs(float(greeks["delta"]))
            result["greeks_source"] = "fyers"
        else:
            result["delta"] = None
            result["greeks_source"] = "bs"   # will be computed later

        return result
    except Exception as e:
        log.error(f"get_option_quote error ({strike}{opt_type}): {e}")
        return None


# ── Black-Scholes delta fallback ───────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Approximation of standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def bs_delta(spot: float, strike: float, opt_type: str,
             days_to_expiry: float, iv: float = 0.15, r: float = 0.065) -> float:
    """
    Black-Scholes delta.
    days_to_expiry: calendar days remaining.
    iv: implied vol (default 15% if unknown).
    """
    try:
        T = max(days_to_expiry / 365, 1e-6)
        d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * T) / (iv * math.sqrt(T))
        if opt_type == "CE":
            return _norm_cdf(d1)
        else:
            return _norm_cdf(d1) - 1   # negative for PE; caller takes abs()
    except Exception as e:
        log.error(f"bs_delta error: {e}")
        return 0.0


def get_straddle_candles(strike: int) -> list[dict] | None:
    """
    Fetch 5-min candles for CE and PE of a strike and return
    combined straddle price candles (CE+PE) with cumulative volume.
    """
    try:
        ce_sym = _option_symbol(strike, "CE")
        pe_sym = _option_symbol(strike, "PE")

        ce_candles = get_candles(ce_sym)
        pe_candles = get_candles(pe_sym)

        if not ce_candles or not pe_candles:
            return None

        # Align by timestamp
        pe_map = {c["ts"]: c for c in pe_candles}
        combined = []
        for c in ce_candles:
            ts = c["ts"]
            if ts in pe_map:
                pe = pe_map[ts]
                combined.append({
                    "ts":     ts,
                    "open":   c["open"]  + pe["open"],
                    "high":   c["high"]  + pe["high"],
                    "low":    c["low"]   + pe["low"],
                    "close":  c["close"] + pe["close"],
                    "volume": c["volume"] + pe["volume"]
                })
        return combined if combined else None
    except Exception as e:
        log.error(f"get_straddle_candles error (strike={strike}): {e}")
        return None
