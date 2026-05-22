"""
position_manager.py
Paper-trade position management.
Uses batched quotes for MTM + delta checks to minimise API calls.
"""

import logging
from datetime import datetime

from config import (
    LOT_SIZE, DELTA_ENTRY_MIN, DELTA_ENTRY_MAX, DELTA_ALERT,
    HEDGE_OFFSET, STRIKE_STEP,
    QTY_NEUTRAL_CE, QTY_NEUTRAL_PE,
    QTY_BULL_CE, QTY_BULL_PE,
    QTY_BEAR_CE, QTY_BEAR_PE,
    QTY_ADD_CHANGE
)
from fyers_data import get_option_quote, get_quotes_batch, bs_delta, _option_symbol
from view_engine import VIEW_BULL, VIEW_BEAR, VIEW_NEUTRAL

log = logging.getLogger(__name__)


# ── Strike selection ────────────────────────────────────────────────────────

def find_delta_strike(spot: float, opt_type: str,
                      days_to_expiry: float) -> tuple[int, float] | tuple[None, None]:
    """
    Use BS delta to estimate the target strike range, then confirm
    with a small batch quote — minimises API calls vs scanning one by one.
    Returns (strike, delta) or (None, None).
    """
    try:
        direction = 1 if opt_type == "CE" else -1
        atm       = round(spot / STRIKE_STEP) * STRIKE_STEP

        # ── Step 1: BS scan to find candidate range (no API calls) ─────────
        candidates = []
        for steps in range(1, 25):
            strike = atm + direction * steps * STRIKE_STEP
            delta  = bs_delta(spot, strike, opt_type, days_to_expiry)
            if DELTA_ENTRY_MIN <= delta <= DELTA_ENTRY_MAX:
                candidates.append((strike, delta))
            elif delta < DELTA_ENTRY_MIN:
                break   # too far OTM

        if not candidates:
            log.warning(f"BS scan: no {opt_type} candidate in delta range near {spot}")
            return None, None

        # ── Step 2: Batch-fetch quotes for candidates (1 API call) ─────────
        syms  = [_option_symbol(s, opt_type) for s, _ in candidates]
        batch = get_quotes_batch(syms)

        # ── Step 3: Pick first candidate with fyers delta if available ──────
        for strike, bs_d in candidates:
            sym = _option_symbol(strike, opt_type)
            if batch and sym in batch and batch[sym].get("greeks_source") == "fyers":
                delta = batch[sym]["delta"]
            else:
                delta = bs_d   # fall back to BS estimate

            if DELTA_ENTRY_MIN <= delta <= DELTA_ENTRY_MAX:
                log.info(f"Selected {opt_type} sell strike {strike} delta={delta:.3f}")
                return strike, delta

        log.warning(f"No {opt_type} strike confirmed in delta range near {spot}")
        return None, None

    except Exception as e:
        log.error(f"find_delta_strike error ({opt_type}): {e}")
        return None, None


def _ltp_from_batch(batch: dict | None, strike: int, opt_type: str) -> float:
    """Extract LTP from a pre-fetched batch dict; fallback to 0."""
    try:
        if not batch:
            return 0.0
        sym = _option_symbol(strike, opt_type)
        return batch.get(sym, {}).get("ltp", 0.0)
    except Exception:
        return 0.0


# ── Leg builder ─────────────────────────────────────────────────────────────

def _make_leg(strike: int, opt_type: str, action: str,
              lots: int, ltp: float, tag: str) -> dict:
    return {
        "symbol":      _option_symbol(strike, opt_type),
        "strike":      strike,
        "opt_type":    opt_type,
        "action":      action,
        "lots":        lots,
        "qty":         lots * LOT_SIZE,
        "entry_price": ltp,
        "entry_time":  datetime.now().isoformat(),
        "tag":         tag,
        "current_ltp": ltp,
        "pnl":         0.0
    }


def _fetch_leg_ltps(strikes_types: list[tuple[int, str]]) -> dict:
    """Batch-fetch LTPs for a list of (strike, opt_type) pairs. Returns sym→ltp dict."""
    try:
        syms  = [_option_symbol(s, t) for s, t in strikes_types]
        batch = get_quotes_batch(syms) or {}
        return {sym: batch.get(sym, {}).get("ltp", 0.0) for sym in syms}
    except Exception as e:
        log.error(f"_fetch_leg_ltps error: {e}")
        return {}


def _build_spread_legs(sell_strike: int, opt_type: str, lots: int,
                       tag: str, hedge_direction: int) -> list[dict]:
    """
    Build sell + hedge pair for one side.
    hedge_direction: +1 for CE (hedge higher), -1 for PE (hedge lower).
    Fetches both LTPs in one batch call.
    """
    hedge_strike = sell_strike + hedge_direction * HEDGE_OFFSET
    ltps = _fetch_leg_ltps([(sell_strike, opt_type), (hedge_strike, opt_type)])
    sell_sym  = _option_symbol(sell_strike,  opt_type)
    hedge_sym = _option_symbol(hedge_strike, opt_type)
    return [
        _make_leg(sell_strike,  opt_type, "SELL", lots, ltps.get(sell_sym,  0.0), tag),
        _make_leg(hedge_strike, opt_type, "BUY",  lots, ltps.get(hedge_sym, 0.0), f"{tag}_hedge"),
    ]


# ── Entry / adjustment builders ─────────────────────────────────────────────

def build_entry_legs(view: str, spot: float,
                     days_to_expiry: float) -> list[dict] | None:
    try:
        ce_lots = {VIEW_NEUTRAL: QTY_NEUTRAL_CE,
                   VIEW_BULL:    QTY_BULL_CE,
                   VIEW_BEAR:    QTY_BEAR_CE}[view]
        pe_lots = {VIEW_NEUTRAL: QTY_NEUTRAL_PE,
                   VIEW_BULL:    QTY_BULL_PE,
                   VIEW_BEAR:    QTY_BEAR_PE}[view]

        ce_strike, _ = find_delta_strike(spot, "CE", days_to_expiry)
        pe_strike, _ = find_delta_strike(spot, "PE", days_to_expiry)

        if ce_strike is None or pe_strike is None:
            log.error("Entry failed – delta strike selection returned None.")
            return None

        # Batch-fetch all 4 LTPs in one call
        ce_hedge = ce_strike + HEDGE_OFFSET
        pe_hedge = pe_strike - HEDGE_OFFSET
        ltps = _fetch_leg_ltps([
            (ce_strike, "CE"), (ce_hedge, "CE"),
            (pe_strike, "PE"), (pe_hedge, "PE")
        ])

        legs = [
            _make_leg(ce_strike, "CE", "SELL", ce_lots,
                      ltps.get(_option_symbol(ce_strike, "CE"), 0.0), "sell"),
            _make_leg(ce_hedge,  "CE", "BUY",  ce_lots,
                      ltps.get(_option_symbol(ce_hedge,  "CE"), 0.0), "hedge"),
            _make_leg(pe_strike, "PE", "SELL", pe_lots,
                      ltps.get(_option_symbol(pe_strike, "PE"), 0.0), "sell"),
            _make_leg(pe_hedge,  "PE", "BUY",  pe_lots,
                      ltps.get(_option_symbol(pe_hedge,  "PE"), 0.0), "hedge"),
        ]
        log.info(
            f"Entry | view={view} | "
            f"CE sell={ce_strike}x{ce_lots} hedge={ce_hedge} | "
            f"PE sell={pe_strike}x{pe_lots} hedge={pe_hedge}"
        )
        return legs
    except Exception as e:
        log.error(f"build_entry_legs error: {e}")
        return None


def build_adjustment_legs(new_view: str, old_view: str,
                           spot: float, days_to_expiry: float) -> list[dict] | None:
    try:
        add = QTY_ADD_CHANGE
        legs = []

        if new_view == VIEW_BEAR:
            strike, _ = find_delta_strike(spot, "CE", days_to_expiry)
            if strike:
                legs = _build_spread_legs(strike, "CE", add, "sell_adj", +1)

        elif new_view == VIEW_BULL:
            strike, _ = find_delta_strike(spot, "PE", days_to_expiry)
            if strike:
                legs = _build_spread_legs(strike, "PE", add, "sell_adj", -1)

        else:   # neutral — add both sides
            ce_strike, _ = find_delta_strike(spot, "CE", days_to_expiry)
            pe_strike, _ = find_delta_strike(spot, "PE", days_to_expiry)
            if ce_strike:
                legs += _build_spread_legs(ce_strike, "CE", add, "sell_adj", +1)
            if pe_strike:
                legs += _build_spread_legs(pe_strike, "PE", add, "sell_adj", -1)

        if not legs:
            log.warning(f"build_adjustment_legs: no legs built ({old_view}→{new_view})")
            return None

        log.info(f"Adjustment | {old_view}→{new_view} | {len(legs)} legs")
        return legs
    except Exception as e:
        log.error(f"build_adjustment_legs error: {e}")
        return None


def build_add_1135_legs(entry_view: str, spot: float,
                        days_to_expiry: float) -> list[dict] | None:
    """11:35 one-time add — same ratio as entry."""
    return build_entry_legs(entry_view, spot, days_to_expiry)


# ── MTM — batched ───────────────────────────────────────────────────────────

def update_positions_mtm(positions: list[dict]) -> list[dict]:
    """
    Refresh all position LTPs and PnL in ONE batched quote call.
    """
    try:
        open_legs = [p for p in positions if not p.get("closed")]
        if not open_legs:
            return positions

        syms  = [_option_symbol(p["strike"], p["opt_type"]) for p in open_legs]
        batch = get_quotes_batch(syms) or {}

        for leg in open_legs:
            sym = _option_symbol(leg["strike"], leg["opt_type"])
            ltp = batch.get(sym, {}).get("ltp", 0.0)
            if ltp == 0:
                continue
            leg["current_ltp"] = ltp
            multiplier = -1 if leg["action"] == "SELL" else 1
            leg["pnl"] = multiplier * (ltp - leg["entry_price"]) * leg["qty"]

    except Exception as e:
        log.error(f"update_positions_mtm error: {e}")
    return positions


def total_pnl(positions: list[dict]) -> float:
    return sum(leg.get("pnl", 0.0) for leg in positions)


# ── Delta breach check — reuses MTM batch ───────────────────────────────────

def check_delta_breaches(positions: list[dict], spot: float,
                         days_to_expiry: float,
                         already_alerted: list[str]) -> list[str]:
    """
    Check delta on all SELL legs using one batched quote call.
    Returns list of newly breached symbols.
    """
    breached = []
    try:
        sell_legs = [p for p in positions
                     if p["action"] == "SELL" and p["symbol"] not in already_alerted
                     and not p.get("closed")]
        if not sell_legs:
            return []

        syms  = [_option_symbol(p["strike"], p["opt_type"]) for p in sell_legs]
        batch = get_quotes_batch(syms) or {}

        for leg in sell_legs:
            sym = _option_symbol(leg["strike"], leg["opt_type"])
            q   = batch.get(sym, {})
            if q.get("greeks_source") == "fyers" and q.get("delta") is not None:
                delta = abs(q["delta"])
            else:
                delta = bs_delta(spot, leg["strike"], leg["opt_type"], days_to_expiry)

            if delta >= DELTA_ALERT:
                log.warning(f"Delta breach: {sym} delta={delta:.3f}")
                breached.append(sym)
    except Exception as e:
        log.error(f"check_delta_breaches error: {e}")
    return breached


# ── Close all ────────────────────────────────────────────────────────────────

def close_all_positions(positions: list[dict]) -> list[dict]:
    """Paper-close all open positions at current LTP."""
    try:
        positions = update_positions_mtm(positions)
        now = datetime.now().isoformat()
        for leg in positions:
            if not leg.get("closed"):
                leg["closed"]     = True
                leg["close_time"] = now
        log.info(f"Closed {len(positions)} legs | PnL: ₹{total_pnl(positions):,.0f}")
    except Exception as e:
        log.error(f"close_all_positions error: {e}")
    return positions
