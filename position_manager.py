"""
position_manager.py
Manages paper-trade positions: entry, adjustment, delta monitoring, close.
All trades are simulated (PAPER_TRADE=True); no real orders are placed.
"""

import logging
from datetime import datetime, date
import math

from config import (
    LOT_SIZE, DELTA_ENTRY_MIN, DELTA_ENTRY_MAX, DELTA_ALERT,
    HEDGE_OFFSET, STRIKE_STEP, EXPIRY_DATE,
    QTY_NEUTRAL_CE, QTY_NEUTRAL_PE,
    QTY_BULL_CE, QTY_BULL_PE,
    QTY_BEAR_CE, QTY_BEAR_PE,
    QTY_ADD_CHANGE, PAPER_TRADE
)
from fyers_data import get_option_quote, bs_delta, _option_symbol
from view_engine import VIEW_BULL, VIEW_BEAR, VIEW_NEUTRAL

log = logging.getLogger(__name__)


# ── Strike selection ───────────────────────────────────────────────────────

def find_delta_strike(spot: float, opt_type: str,
                      days_to_expiry: float) -> tuple[int, float] | tuple[None, None]:
    """
    Scan OTM strikes from nearest to farthest; return first with delta in [0.10, 0.15].
    Returns (strike, delta) or (None, None).
    """
    try:
        direction = 1 if opt_type == "CE" else -1
        atm = round(spot / STRIKE_STEP) * STRIKE_STEP

        for steps in range(1, 30):
            strike = atm + direction * steps * STRIKE_STEP
            quote  = get_option_quote(strike, opt_type)

            if quote is None:
                continue

            if quote.get("greeks_source") == "fyers" and quote.get("delta") is not None:
                delta = abs(quote["delta"])
            else:
                iv    = 0.15   # default IV; improve later if IV data available
                delta = abs(bs_delta(spot, strike, opt_type, days_to_expiry, iv))

            if DELTA_ENTRY_MIN <= delta <= DELTA_ENTRY_MAX:
                log.info(f"Found {opt_type} sell strike {strike} delta={delta:.3f}")
                return strike, delta

            if delta < DELTA_ENTRY_MIN:
                break   # going too far OTM

        log.warning(f"No suitable {opt_type} strike found near {spot}")
        return None, None
    except Exception as e:
        log.error(f"find_delta_strike error ({opt_type}): {e}")
        return None, None


def _current_ltp(strike: int, opt_type: str) -> float:
    """Fetch current LTP for a leg; return 0 on failure."""
    try:
        q = get_option_quote(strike, opt_type)
        return q["ltp"] if q else 0.0
    except Exception:
        return 0.0


# ── Leg builders ───────────────────────────────────────────────────────────

def _make_leg(strike: int, opt_type: str, action: str,
              lots: int, ltp: float, tag: str) -> dict:
    return {
        "symbol":    _option_symbol(strike, opt_type),
        "strike":    strike,
        "opt_type":  opt_type,
        "action":    action,           # SELL or BUY (hedge)
        "lots":      lots,
        "qty":       lots * LOT_SIZE,
        "entry_price": ltp,
        "entry_time":  datetime.now().isoformat(),
        "tag":         tag,            # "sell" or "hedge"
        "current_ltp": ltp,
        "pnl":         0.0
    }


def build_entry_legs(view: str, spot: float,
                     days_to_expiry: float) -> list[dict] | None:
    """
    Build all 4 legs for initial entry based on view.
    Returns list of leg dicts or None on failure.
    """
    try:
        ce_lots = {VIEW_NEUTRAL: QTY_NEUTRAL_CE,
                   VIEW_BULL:    QTY_BULL_CE,
                   VIEW_BEAR:    QTY_BEAR_CE}[view]
        pe_lots = {VIEW_NEUTRAL: QTY_NEUTRAL_PE,
                   VIEW_BULL:    QTY_BULL_PE,
                   VIEW_BEAR:    QTY_BEAR_PE}[view]

        ce_sell_strike, _ = find_delta_strike(spot, "CE", days_to_expiry)
        pe_sell_strike, _ = find_delta_strike(spot, "PE", days_to_expiry)

        if ce_sell_strike is None or pe_sell_strike is None:
            log.error("Cannot build entry – strike selection failed.")
            return None

        ce_hedge_strike = ce_sell_strike + HEDGE_OFFSET
        pe_hedge_strike = pe_sell_strike - HEDGE_OFFSET

        legs = [
            _make_leg(ce_sell_strike,   "CE", "SELL", ce_lots,
                      _current_ltp(ce_sell_strike,   "CE"), "sell"),
            _make_leg(ce_hedge_strike,  "CE", "BUY",  ce_lots,
                      _current_ltp(ce_hedge_strike,  "CE"), "hedge"),
            _make_leg(pe_sell_strike,   "PE", "SELL", pe_lots,
                      _current_ltp(pe_sell_strike,   "PE"), "sell"),
            _make_leg(pe_hedge_strike,  "PE", "BUY",  pe_lots,
                      _current_ltp(pe_hedge_strike,  "PE"), "hedge"),
        ]
        log.info(
            f"Entry legs built | view={view} | "
            f"CE sell={ce_sell_strike}x{ce_lots} hedge={ce_hedge_strike} | "
            f"PE sell={pe_sell_strike}x{pe_lots} hedge={pe_hedge_strike}"
        )
        return legs
    except Exception as e:
        log.error(f"build_entry_legs error: {e}")
        return None


def build_adjustment_legs(new_view: str, old_view: str,
                           spot: float, days_to_expiry: float) -> list[dict] | None:
    """
    On confirmed view change: add QTY_ADD_CHANGE lots on newly favored side + hedge.
    Bearish  → add CE sell side
    Bullish  → add PE sell side
    Neutral  → add both sides equally (QTY_ADD_CHANGE each)
    """
    try:
        legs = []
        add = QTY_ADD_CHANGE

        if new_view == VIEW_BEAR:
            strike, _ = find_delta_strike(spot, "CE", days_to_expiry)
            if strike:
                hedge = strike + HEDGE_OFFSET
                legs += [
                    _make_leg(strike, "CE", "SELL", add,
                              _current_ltp(strike, "CE"), "sell_adj"),
                    _make_leg(hedge,  "CE", "BUY",  add,
                              _current_ltp(hedge,  "CE"), "hedge_adj"),
                ]
        elif new_view == VIEW_BULL:
            strike, _ = find_delta_strike(spot, "PE", days_to_expiry)
            if strike:
                hedge = strike - HEDGE_OFFSET
                legs += [
                    _make_leg(strike, "PE", "SELL", add,
                              _current_ltp(strike, "PE"), "sell_adj"),
                    _make_leg(hedge,  "PE", "BUY",  add,
                              _current_ltp(hedge,  "PE"), "hedge_adj"),
                ]
        else:  # neutral
            ce_strike, _ = find_delta_strike(spot, "CE", days_to_expiry)
            pe_strike, _ = find_delta_strike(spot, "PE", days_to_expiry)
            if ce_strike and pe_strike:
                legs += [
                    _make_leg(ce_strike,               "CE", "SELL", add,
                              _current_ltp(ce_strike, "CE"), "sell_adj"),
                    _make_leg(ce_strike + HEDGE_OFFSET, "CE", "BUY",  add,
                              _current_ltp(ce_strike + HEDGE_OFFSET, "CE"), "hedge_adj"),
                    _make_leg(pe_strike,               "PE", "SELL", add,
                              _current_ltp(pe_strike, "PE"), "sell_adj"),
                    _make_leg(pe_strike - HEDGE_OFFSET, "PE", "BUY",  add,
                              _current_ltp(pe_strike - HEDGE_OFFSET, "PE"), "hedge_adj"),
                ]
        log.info(f"Adjustment legs built | {old_view}→{new_view} | {len(legs)} legs")
        return legs if legs else None
    except Exception as e:
        log.error(f"build_adjustment_legs error: {e}")
        return None


def build_add_1135_legs(entry_view: str, spot: float,
                        days_to_expiry: float) -> list[dict] | None:
    """One-time 11:35 add – same ratio as entry."""
    return build_entry_legs(entry_view, spot, days_to_expiry)


# ── MTM update ─────────────────────────────────────────────────────────────

def update_positions_mtm(positions: list[dict]) -> list[dict]:
    """Refresh current_ltp and pnl for all open positions."""
    for leg in positions:
        try:
            ltp = _current_ltp(leg["strike"], leg["opt_type"])
            if ltp == 0:
                continue
            leg["current_ltp"] = ltp
            multiplier = -1 if leg["action"] == "SELL" else 1
            leg["pnl"] = multiplier * (ltp - leg["entry_price"]) * leg["qty"]
        except Exception as e:
            log.error(f"update_positions_mtm error ({leg.get('symbol')}): {e}")
    return positions


def total_pnl(positions: list[dict]) -> float:
    return sum(leg.get("pnl", 0) for leg in positions)


# ── Delta monitoring ───────────────────────────────────────────────────────

def check_delta_breaches(positions: list[dict], spot: float,
                         days_to_expiry: float,
                         already_alerted: list[str]) -> list[str]:
    """
    Return list of symbols that have breached delta 0.35 and not yet alerted.
    """
    breached = []
    try:
        for leg in positions:
            if leg["action"] != "SELL":
                continue
            sym = leg["symbol"]
            if sym in already_alerted:
                continue
            q = get_option_quote(leg["strike"], leg["opt_type"])
            if q and q.get("greeks_source") == "fyers" and q.get("delta") is not None:
                delta = abs(q["delta"])
            else:
                delta = abs(bs_delta(spot, leg["strike"], leg["opt_type"], days_to_expiry))
            if delta >= DELTA_ALERT:
                log.warning(f"Delta breach! {sym} delta={delta:.3f}")
                breached.append(sym)
    except Exception as e:
        log.error(f"check_delta_breaches error: {e}")
    return breached


# ── Close all ─────────────────────────────────────────────────────────────

def close_all_positions(positions: list[dict]) -> list[dict]:
    """
    Paper-close all positions at current LTP.
    Updates pnl and marks as closed.
    """
    try:
        positions = update_positions_mtm(positions)
        for leg in positions:
            leg["closed"]     = True
            leg["close_time"] = datetime.now().isoformat()
        log.info(f"Closed {len(positions)} legs | Total PnL: {total_pnl(positions):.2f}")
        return positions
    except Exception as e:
        log.error(f"close_all_positions error: {e}")
        return positions
