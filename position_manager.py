"""
position_manager.py
Paper-trade position management:
  - Score-based entry sizing
  - 12:00 one-time add
  - Rebalance: close to nearest ratio multiple, add where needed
  - Max 10 lots per side
  - Max 4 adjustments per day
  - Trailing SL (alert only)
  - Post-4-adj exit check
"""

import logging
from datetime import datetime

from config import (
    LOT_SIZE, DELTA_ENTRY_MIN, DELTA_ENTRY_MAX, DELTA_ALERT,
    HEDGE_OFFSET, STRIKE_STEP, MIN_SELL_LTP, MIN_STRIKE_DIST,
    MAX_LOTS_CE, MAX_LOTS_PE,
    ENTRY_LOTS, ADD_1200_LOTS, TARGET_RATIO,
    TSL_ACTIVATE_PNL, TSL_INITIAL_GAP, TSL_STEP_PROFIT, TSL_STEP_MOVE,
    POST_ADJ_EXIT_PNL, POST_ADJ_EXIT_LOSS, MAX_ADJUSTMENTS
)
from fyers_data import get_quotes_batch, bs_delta, _option_symbol

log = logging.getLogger(__name__)


# ── Lot helpers ──────────────────────────────────────────────────────────────

def open_sell_lots(positions: list[dict], opt_type: str) -> int:
    return sum(p["lots"] for p in positions
               if p["action"] == "SELL"
               and p["opt_type"] == opt_type
               and not p.get("closed"))


def _cap_add(lots: int, opt_type: str, positions: list[dict]) -> int:
    cap = MAX_LOTS_CE if opt_type == "CE" else MAX_LOTS_PE
    return max(0, min(lots, cap - open_sell_lots(positions, opt_type)))


# ── Strike selection ─────────────────────────────────────────────────────────

def find_delta_strike(spot: float, opt_type: str,
                      dte: float) -> tuple[int, float, float] | tuple[None, None, None]:
    """
    Find sell strike satisfying ALL three conditions:
      1. Delta between DELTA_ENTRY_MIN and DELTA_ENTRY_MAX (0.10-0.15)
      2. LTP > MIN_SELL_LTP (> Rs 2)
      3. Strike at least MIN_STRIKE_DIST points from ATM (>= 300 pts)
    Returns (strike, delta, ltp) or (None, None, None).
    """
    try:
        direction = 1 if opt_type == "CE" else -1
        atm       = round(spot / STRIKE_STEP) * STRIKE_STEP

        # Min distance relaxes when DTE is low — avoids no-candidate failure
        # DTE < 5: 200pts,  DTE < 10: 250pts,  else: 300pts (config value)
        if dte < 1:
            min_dist = 200
        elif dte < 3 :
            min_dist = 250
        else:
            min_dist = MIN_STRIKE_DIST

        candidates = []
        for steps in range(1, 35):
            strike   = atm + direction * steps * STRIKE_STEP
            distance = abs(strike - atm)

            # Condition 3: min distance from ATM (DTE-adjusted)
            if distance < min_dist:
                continue

            delta = bs_delta(spot, strike, opt_type, dte)
            if DELTA_ENTRY_MIN <= delta <= DELTA_ENTRY_MAX:
                candidates.append((strike, delta))
            elif delta < DELTA_ENTRY_MIN:
                break   # going too far OTM, stop scanning

        if not candidates:
            log.warning(f"No {opt_type} BS candidate >={min_dist}pts from ATM near {spot} (DTE={dte})")
            return None, None, None

        # Batch fetch LTPs for all candidates in one call
        syms  = [_option_symbol(s, opt_type) for s, _ in candidates]
        batch = get_quotes_batch(syms) or {}

        for strike, bs_d in candidates:
            sym = _option_symbol(strike, opt_type)
            q   = batch.get(sym, {})
            ltp = q.get("ltp", 0.0)

            # Condition 2: LTP > Rs 2
            if ltp < MIN_SELL_LTP:
                log.info(f"Skip {opt_type} {strike}: LTP={ltp:.2f} < {MIN_SELL_LTP}")
                continue

            # Condition 1: delta (use Fyers greek if available, else BS)
            delta = (q["delta"] if q.get("greeks_source") == "fyers"
                     and q.get("delta") else bs_d)
            if DELTA_ENTRY_MIN <= delta <= DELTA_ENTRY_MAX:
                log.info(f"Selected {opt_type} {strike} | delta={delta:.3f} ltp={ltp:.2f} dist={abs(strike-atm)}pts")
                return strike, delta, ltp

        log.warning(f"No valid {opt_type} strike found (all failed LTP/delta/dist check)")
        return None, None, None
    except Exception as e:
        log.error(f"find_delta_strike ({opt_type}): {e}")
        return None, None, None


# ── Leg builders ─────────────────────────────────────────────────────────────

def _fetch_ltps(pairs: list[tuple[int, str]]) -> dict:
    try:
        syms  = [_option_symbol(s, t) for s, t in pairs]
        batch = get_quotes_batch(syms) or {}
        return {sym: batch.get(sym, {}).get("ltp", 0.0) for sym in syms}
    except Exception as e:
        log.error(f"_fetch_ltps: {e}")
        return {}


def _make_leg(strike: int, opt_type: str, action: str,
              lots: int, ltp: float, tag: str,
              delta: float = 0.0) -> dict:
    return {
        "symbol":      _option_symbol(strike, opt_type),
        "strike":      strike,
        "opt_type":    opt_type,
        "action":      action,
        "lots":        lots,
        "qty":         lots * LOT_SIZE,
        "entry_price": ltp,
        "entry_time":  datetime.now().strftime("%H:%M:%S"),
        "entry_delta": round(delta, 3),
        "tag":         tag,
        "current_ltp": ltp,
        "pnl":         0.0
    }


def _add_spread(sell_strike: int, opt_type: str, lots: int,
                tag: str, positions: list[dict],
                sell_delta: float = 0.0, sell_ltp: float = 0.0) -> list[dict]:
    """Add sell+hedge respecting max-lot cap."""
    lots = _cap_add(lots, opt_type, positions)
    if lots <= 0:
        return []
    hdir         = 1 if opt_type == "CE" else -1
    hedge_strike = sell_strike + hdir * HEDGE_OFFSET
    hedge_ltp    = _fetch_ltps([(hedge_strike, opt_type)]).get(
                       _option_symbol(hedge_strike, opt_type), 0.0)
    sell_price   = sell_ltp if sell_ltp > 0 else _fetch_ltps(
                       [(sell_strike, opt_type)]).get(
                       _option_symbol(sell_strike, opt_type), 0.0)
    return [
        _make_leg(sell_strike,  opt_type, "SELL", lots, sell_price,  tag,          sell_delta),
        _make_leg(hedge_strike, opt_type, "BUY",  lots, hedge_ltp,   f"{tag}_h",   0.0),
    ]


def _close_sell_lots(positions: list[dict], opt_type: str,
                     lots_to_close: int) -> list[dict]:
    """
    Paper-close the oldest open SELL legs on opt_type, up to lots_to_close.
    Matching BUY (hedge) legs are also closed.
    Returns list of newly closed leg symbols.
    """
    closed_syms = []
    remaining   = lots_to_close
    # Sort by entry_time ascending (close oldest first)
    sell_legs = sorted(
        [p for p in positions if p["action"] == "SELL"
         and p["opt_type"] == opt_type and not p.get("closed")],
        key=lambda x: x.get("entry_time", "")
    )
    for leg in sell_legs:
        if remaining <= 0:
            break
        close_lots = min(leg["lots"], remaining)
        if close_lots == leg["lots"]:
            leg["closed"]     = True
            leg["close_time"] = datetime.now().isoformat()
            closed_syms.append(leg["symbol"])
        else:
            # Partial close: split the leg
            leg["lots"] -= close_lots
            leg["qty"]   = leg["lots"] * LOT_SIZE
            # Create a closed record for the partial
            closed_leg = dict(leg)
            closed_leg["lots"]       = close_lots
            closed_leg["qty"]        = close_lots * LOT_SIZE
            closed_leg["closed"]     = True
            closed_leg["close_time"] = datetime.now().isoformat()
            closed_leg["tag"]        = leg["tag"] + "_partial"
            positions.append(closed_leg)
            closed_syms.append(leg["symbol"])
        remaining -= close_lots

    # Close matching hedge legs proportionally
    hedge_legs = sorted(
        [p for p in positions if p["action"] == "BUY"
         and p["opt_type"] == opt_type and not p.get("closed")],
        key=lambda x: x.get("entry_time", "")
    )
    hedge_rem = lots_to_close
    for leg in hedge_legs:
        if hedge_rem <= 0:
            break
        close_lots = min(leg["lots"], hedge_rem)
        if close_lots == leg["lots"]:
            leg["closed"]     = True
            leg["close_time"] = datetime.now().isoformat()
        else:
            leg["lots"] -= close_lots
            leg["qty"]   = leg["lots"] * LOT_SIZE
            closed_leg = dict(leg)
            closed_leg["lots"]       = close_lots
            closed_leg["qty"]        = close_lots * LOT_SIZE
            closed_leg["closed"]     = True
            closed_leg["close_time"] = datetime.now().isoformat()
            positions.append(closed_leg)
        hedge_rem -= close_lots

    log.info(f"Closed {lots_to_close - remaining} {opt_type} sell lots")
    return closed_syms


# ── Rebalance: nearest ratio multiple ────────────────────────────────────────

def compute_rebalance(current_ce: int, current_pe: int,
                      target_ce: int, target_pe: int) -> tuple[int, int, int, int]:
    """
    Find highest multiplier N such that N*target fits within current on both sides.
    If one side needs more than current, add on that side and close other.
    Returns (ce_to_add, pe_to_add, ce_to_close, pe_to_close).
    """
    # Try highest N where both fit
    max_n_ce = current_ce // target_ce if target_ce > 0 else 0
    max_n_pe = current_pe // target_pe if target_pe > 0 else 0
    n = min(max_n_ce, max_n_pe)

    if n >= 1:
        # Both sides can reach n*target by closing
        goal_ce = n * target_ce
        goal_pe = n * target_pe
        ce_to_close = max(0, current_ce - goal_ce)
        pe_to_close = max(0, current_pe - goal_pe)
        return 0, 0, ce_to_close, pe_to_close
    else:
        # n=0: at least one side is below target×1
        # Add the deficit side, close other to match ratio
        need_ce = target_ce - current_ce
        need_pe = target_pe - current_pe
        ce_to_add   = max(0, need_ce)
        pe_to_add   = max(0, need_pe)
        ce_to_close = max(0, current_ce - target_ce)
        pe_to_close = max(0, current_pe - target_pe)
        return ce_to_add, pe_to_add, ce_to_close, pe_to_close


# ── Entry ─────────────────────────────────────────────────────────────────────

def build_entry_legs(label: str, spot: float, dte: float) -> list[dict] | None:
    try:
        ce_lots, pe_lots = ENTRY_LOTS.get(label, (3, 3))
        ce_strike, ce_delta, ce_ltp = find_delta_strike(spot, "CE", dte)
        pe_strike, pe_delta, pe_ltp = find_delta_strike(spot, "PE", dte)
        if ce_strike is None or pe_strike is None:
            log.error("Entry: strike selection failed")
            return None
        ce_hedge = ce_strike + HEDGE_OFFSET
        pe_hedge = pe_strike - HEDGE_OFFSET
        ce_hedge_ltp = _fetch_ltps([(ce_hedge, "CE")]).get(_option_symbol(ce_hedge, "CE"), 0.0)
        pe_hedge_ltp = _fetch_ltps([(pe_hedge, "PE")]).get(_option_symbol(pe_hedge, "PE"), 0.0)
        legs = [
            _make_leg(ce_strike, "CE", "SELL", ce_lots, ce_ltp or 0.0, "sell",  ce_delta or 0.0),
            _make_leg(ce_hedge,  "CE", "BUY",  ce_lots, ce_hedge_ltp,  "hedge", 0.0),
            _make_leg(pe_strike, "PE", "SELL", pe_lots, pe_ltp or 0.0, "sell",  pe_delta or 0.0),
            _make_leg(pe_hedge,  "PE", "BUY",  pe_lots, pe_hedge_ltp,  "hedge", 0.0),
        ]
        log.info(f"Entry | label={label} CE {ce_strike}x{ce_lots} d={ce_delta:.3f} PE {pe_strike}x{pe_lots} d={pe_delta:.3f}")
        return legs
    except Exception as e:
        log.error(f"build_entry_legs: {e}")
        return None


# ── 12:00 addition ────────────────────────────────────────────────────────────

def build_1200_legs(entry_label: str, current_label: str,
                    spot: float, dte: float,
                    positions: list[dict]) -> list[dict] | None:
    try:
        def _fam(l):
            if l in ("very_bullish", "bullish"):   return "bullish"
            if l in ("very_bearish", "bearish"):   return "bearish"
            return "neutral"
        key = (_fam(entry_label), _fam(current_label))
        ce_add, pe_add = ADD_1200_LOTS.get(key, (0, 0))
        log.info(f"12:00 add | key={key} ce={ce_add} pe={pe_add}")
        legs = []
        if ce_add > 0:
            s, d, ltp = find_delta_strike(spot, "CE", dte)
            if s:
                legs += _add_spread(s, "CE", ce_add, "sell_1200", positions, d or 0.0, ltp or 0.0)
        if pe_add > 0:
            s, d, ltp = find_delta_strike(spot, "PE", dte)
            if s:
                legs += _add_spread(s, "PE", pe_add, "sell_1200", positions, d or 0.0, ltp or 0.0)
        return legs if legs else None
    except Exception as e:
        log.error(f"build_1200_legs: {e}")
        return None


# ── Rebalance on view change ──────────────────────────────────────────────────

def build_rebalance_legs(new_label: str, spot: float, dte: float,
                         positions: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Rebalance to nearest multiple of TARGET_RATIO[new_label].
    Returns (new_legs_to_add, closed_symbols).
    Modifies positions in-place for closes.
    """
    new_legs     = []
    closed_syms  = []
    try:
        target_ce, target_pe = TARGET_RATIO.get(new_label, (4, 4))
        cur_ce = open_sell_lots(positions, "CE")
        cur_pe = open_sell_lots(positions, "PE")

        ce_add, pe_add, ce_close, pe_close = compute_rebalance(
            cur_ce, cur_pe, target_ce, target_pe
        )
        log.info(f"Rebalance | label={new_label} | "
                 f"cur CE={cur_ce} PE={cur_pe} | "
                 f"target CE={target_ce} PE={target_pe} | "
                 f"add CE={ce_add} PE={pe_add} | "
                 f"close CE={ce_close} PE={pe_close}")

        # Close first
        if ce_close > 0:
            closed_syms += _close_sell_lots(positions, "CE", ce_close)
        if pe_close > 0:
            closed_syms += _close_sell_lots(positions, "PE", pe_close)

        # Then add (respecting max cap after closes)
        if ce_add > 0:
            s, d, ltp = find_delta_strike(spot, "CE", dte)
            if s:
                new_legs += _add_spread(s, "CE", ce_add, "sell_reb", positions, d or 0.0, ltp or 0.0)
        if pe_add > 0:
            s, d, ltp = find_delta_strike(spot, "PE", dte)
            if s:
                new_legs += _add_spread(s, "PE", pe_add, "sell_reb", positions, d or 0.0, ltp or 0.0)

    except Exception as e:
        log.error(f"build_rebalance_legs: {e}")
    return new_legs, closed_syms


# ── Adjustment trigger check ──────────────────────────────────────────────────

def should_adjust(entry_label: str, confirmed_score: float,
                  neutral_adjusted: bool) -> bool:
    """
    Returns True if score has crossed the adjustment threshold
    for the given entry direction.
    neutral_adjusted: True if neutral entry has already been adjusted once.
    After neutral adjustment, thresholds stay at ±3.0 forever.
    """
    try:
        from config import ADJ_TRIGGER

        def _fam(l):
            if l in ("very_bullish", "bullish"): return "bullish"
            if l in ("very_bearish", "bearish"): return "bearish"
            return "neutral"

        fam = _fam(entry_label)
        # Neutral adjusted: always use neutral thresholds
        if neutral_adjusted and fam == "neutral":
            fam = "neutral"

        below_thresh, above_thresh = ADJ_TRIGGER.get(fam, (None, None))
        if below_thresh is not None and confirmed_score < below_thresh:
            log.info(f"Adjust trigger: score {confirmed_score:+.1f} < {below_thresh}")
            return True
        if above_thresh is not None and confirmed_score > above_thresh:
            log.info(f"Adjust trigger: score {confirmed_score:+.1f} > {above_thresh}")
            return True
        return False
    except Exception as e:
        log.error(f"should_adjust: {e}")
        return False


# ── Trailing SL ───────────────────────────────────────────────────────────────

def compute_tsl(peak_pnl: float) -> float | None:
    """
    Returns TSL level if peak >= TSL_ACTIVATE_PNL, else None.
    TSL = 1000 + ((peak - 1500) // 200) * 100
    """
    if peak_pnl < TSL_ACTIVATE_PNL:
        return None
    steps = int((peak_pnl - TSL_ACTIVATE_PNL) // TSL_STEP_PROFIT)
    tsl   = (TSL_ACTIVATE_PNL - TSL_INITIAL_GAP) + steps * TSL_STEP_MOVE
    return round(tsl, 2)


# ── MTM ───────────────────────────────────────────────────────────────────────

def update_positions_mtm(positions: list[dict]) -> list[dict]:
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
            mult       = -1 if leg["action"] == "SELL" else 1
            leg["pnl"] = mult * (ltp - leg["entry_price"]) * leg["qty"]
    except Exception as e:
        log.error(f"update_positions_mtm: {e}")
    return positions


def total_pnl(positions: list[dict]) -> float:
    return sum(p.get("pnl", 0.0) for p in positions)


# ── Delta breach ──────────────────────────────────────────────────────────────

def check_delta_breaches(positions, spot, dte, already_alerted):
    breached = []
    try:
        sell_legs = [p for p in positions if p["action"] == "SELL"
                     and not p.get("closed")
                     and p["symbol"] not in already_alerted]
        if not sell_legs:
            return []
        syms  = [_option_symbol(p["strike"], p["opt_type"]) for p in sell_legs]
        batch = get_quotes_batch(syms) or {}
        for leg in sell_legs:
            sym   = _option_symbol(leg["strike"], leg["opt_type"])
            q     = batch.get(sym, {})
            delta = (abs(q["delta"]) if q.get("greeks_source") == "fyers"
                     and q.get("delta") else
                     bs_delta(spot, leg["strike"], leg["opt_type"], dte))
            if delta >= DELTA_ALERT:
                log.warning(f"Delta breach: {sym} {delta:.3f}")
                breached.append(sym)
    except Exception as e:
        log.error(f"check_delta_breaches: {e}")
    return breached


# ── Close all ─────────────────────────────────────────────────────────────────

def close_all_positions(positions: list[dict]) -> list[dict]:
    try:
        positions = update_positions_mtm(positions)
        now = datetime.now().isoformat()
        for leg in positions:
            if not leg.get("closed"):
                leg["closed"]     = True
                leg["close_time"] = now
        log.info(f"Closed all | PnL: {total_pnl(positions):,.0f}")
    except Exception as e:
        log.error(f"close_all_positions: {e}")
    return positions
