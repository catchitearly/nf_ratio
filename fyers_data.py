"""
fyers_data.py
Fyers API data layer using fyers_apiv3 SDK.
Key design decisions:
  - Auth check once per process (singleton)
  - Multi-symbol quote batching to minimise API calls
  - BS delta used to pre-estimate strike before API confirmation
  - All calls wrapped in try/except
"""

import logging
import math
from datetime import date

from fyers_apiv3 import fyersModel

from config import (
    FYERS_CLIENT_ID, FYERS_ACCESS_TOKEN,
    INDEX_SYMBOL, EXPIRY_STR, INTERVAL, STRIKE_STEP
)

log = logging.getLogger(__name__)

# ── Singleton client ────────────────────────────────────────────────────────
_fyers: fyersModel.FyersModel | None = None


def get_fyers() -> fyersModel.FyersModel | None:
    """Return authenticated FyersModel; initialised once per process."""
    global _fyers
    if _fyers is not None:
        return _fyers
    try:
        if not FYERS_CLIENT_ID or not FYERS_ACCESS_TOKEN:
            log.error("FYERS credentials missing in environment.")
            return None
        client = fyersModel.FyersModel(
            client_id=FYERS_CLIENT_ID,
            token=FYERS_ACCESS_TOKEN,
            log_path=""
        )
        profile = client.get_profile()
        if profile.get("s") != "ok":
            log.error(f"Fyers auth failed: {profile}")
            return None
        log.info(f"Fyers authenticated: {profile.get('data',{}).get('name','')}")
        _fyers = client
        return _fyers
    except Exception as e:
        log.error(f"get_fyers error: {e}")
        return None


# ── Symbol builder ──────────────────────────────────────────────────────────

def _option_symbol(strike: int, opt_type: str) -> str:
    """
    Build Fyers option symbol.
    Format confirmed from working code: NSE:NIFTY{EXPIRY_STR}{strike}{CE/PE}
    e.g. NSE:NIFTY26MAY2623700CE  (EXPIRY_STR = '26MAY26')
    """
    return f"NSE:NIFTY{EXPIRY_STR}{strike}{opt_type}"


# ── Candle fetcher ──────────────────────────────────────────────────────────

def _fetch_candles_raw(symbol: str) -> list | None:
    """Fetch today's 5-min candles via SDK. Returns raw [[epoch,...]] or None."""
    try:
        fyers = get_fyers()
        if fyers is None:
            return None
        today   = date.today().strftime("%Y-%m-%d")
        payload = {
            "symbol":      symbol,
            "resolution":  INTERVAL,
            "date_format": "1",
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


def get_candles(symbol: str) -> list[dict] | None:
    """Return today's 5-min candles as list of {ts,open,high,low,close,volume}."""
    try:
        raw = _fetch_candles_raw(symbol)
        if not raw:
            return None
        return [{"ts": c[0], "open": c[1], "high": c[2],
                 "low": c[3], "close": c[4], "volume": c[5]}
                for c in raw]
    except Exception as e:
        log.error(f"get_candles error ({symbol}): {e}")
        return None


def get_915_candle_close() -> float | None:
    """Return 9:15 candle close for Nifty index (first candle of today)."""
    try:
        raw = _fetch_candles_raw(INDEX_SYMBOL)
        if not raw:
            return None
        close = float(raw[0][4])
        log.info(f"9:15 candle close: {close}")
        return close
    except Exception as e:
        log.error(f"get_915_candle_close error: {e}")
        return None


# ── Quotes — batched ────────────────────────────────────────────────────────

def get_quotes_batch(symbols: list[str]) -> dict[str, dict] | None:
    """
    Fetch quotes for multiple symbols in ONE API call.
    Returns {symbol: {ltp, delta, greeks_source, ...}} or None.
    Fyers accepts comma-separated symbols string.
    """
    try:
        fyers = get_fyers()
        if fyers is None:
            return None
        # Fyers accepts up to ~50 symbols comma-separated
        joined = ",".join(symbols)
        resp   = fyers.quotes(data={"symbols": joined})
        if resp.get("s") != "ok" or not resp.get("d"):
            log.warning(f"get_quotes_batch failed: {resp.get('message','')}")
            return None

        result = {}
        for item in resp["d"]:
            sym = item.get("n", "")
            v   = item.get("v", {})
            entry = {
                "ltp":    float(v.get("lp", 0)),
                "bid":    float(v.get("bid_price", 0)),
                "ask":    float(v.get("ask_price", 0)),
                "volume": int(v.get("volume", 0)),
            }
            greeks = v.get("greeks") or {}
            if greeks.get("delta") is not None:
                entry["delta"]         = abs(float(greeks["delta"]))
                entry["greeks_source"] = "fyers"
            else:
                entry["delta"]         = None
                entry["greeks_source"] = "bs"
            result[sym] = entry
        return result
    except Exception as e:
        log.error(f"get_quotes_batch error: {e}")
        return None


def get_spot_price() -> float | None:
    """Fetch latest Nifty spot LTP."""
    try:
        fyers = get_fyers()
        if fyers is None:
            return None
        resp = fyers.quotes(data={"symbols": INDEX_SYMBOL})
        if resp.get("s") != "ok" or not resp.get("d"):
            log.error(f"get_spot_price error: {resp}")
            return None
        ltp = resp["d"][0]["v"]["lp"]
        log.info(f"Spot: {ltp}")
        return float(ltp)
    except Exception as e:
        log.error(f"get_spot_price error: {e}")
        return None


def get_option_quote(strike: int, opt_type: str) -> dict | None:
    """Single option quote — used only when batching isn't possible."""
    try:
        sym    = _option_symbol(strike, opt_type)
        batch  = get_quotes_batch([sym])
        if not batch or sym not in batch:
            return None
        q = batch[sym]
        return {"symbol": sym, "strike": strike, "opt_type": opt_type, **q}
    except Exception as e:
        log.error(f"get_option_quote error ({strike}{opt_type}): {e}")
        return None


# ── Straddle candles ────────────────────────────────────────────────────────

def get_straddle_candles(strike: int) -> list[dict] | None:
    """
    Fetch CE+PE candles and return combined straddle candles aligned by timestamp.
    """
    try:
        ce_raw = _fetch_candles_raw(_option_symbol(strike, "CE"))
        pe_raw = _fetch_candles_raw(_option_symbol(strike, "PE"))

        if not ce_raw or not pe_raw:
            log.warning(f"Missing candles for straddle {strike}")
            return None

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
            log.warning(f"No aligned candles for straddle {strike}")
            return None

        log.info(f"Straddle {strike}: {len(combined)} bars")
        return combined
    except Exception as e:
        log.error(f"get_straddle_candles error ({strike}): {e}")
        return None


# ── Black-Scholes delta ─────────────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def bs_delta(spot: float, strike: float, opt_type: str,
             days_to_expiry: float, iv: float = 0.15, r: float = 0.065) -> float:
    """Black-Scholes delta (abs value returned for both CE and PE)."""
    try:
        T  = max(days_to_expiry / 365, 1e-6)
        d1 = (math.log(spot / strike) + (r + 0.5 * iv**2) * T) / (iv * math.sqrt(T))
        raw = _norm_cdf(d1) if opt_type == "CE" else _norm_cdf(d1) - 1
        return abs(raw)
    except Exception as e:
        log.error(f"bs_delta error: {e}")
        return 0.0
