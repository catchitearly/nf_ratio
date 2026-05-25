"""
strategy.py
Main orchestrator. Called every 5 min by main.py.

Timeline:
  09:15 → fix ATM from 9:15 candle
  10:30 → entry based on score label
  12:00 → one-time addition (if no adj done yet)
  any   → adjustment on score threshold breach (max 4 total incl 12pm add)
  any   → post-4-adj: close all if |pnl| > 500 (checked every 5 min)
  any   → TSL alert when pnl <= tsl level (once only)
  15:00 → close all
"""

import logging
from datetime import datetime, date, time, timedelta
import pytz

from config import (
    ENTRY_TIME, ADD_1200_TIME, CLOSE_TIME, MARKET_OPEN,
    EXPIRY_DATE, STRIKE_STEP,
    MAX_ADJUSTMENTS, POST_ADJ_EXIT_PNL, POST_ADJ_EXIT_LOSS,
    TSL_ACTIVATE_PNL
)
from state_manager import load_state, save_state, reset_daily_state, append_error
from fyers_data import get_spot_price, get_915_candle_close
from view_engine import compute_view, update_view_state
from position_manager import (
    build_entry_legs, build_1200_legs, build_rebalance_legs,
    update_positions_mtm, close_all_positions,
    check_delta_breaches, total_pnl,
    should_adjust, compute_tsl, open_sell_lots
)
from telegram_bot import (
    send_entry_alert, send_adjustment_alert, send_add_1135_alert,
    send_delta_alert, send_close_alert, send_error_alert,
    send_status, send_tsl_alert, send_post_adj_close_alert
)
from dashboard_gen import generate_dashboard

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


def _now_ist():
    return datetime.now(IST)

def _now_time():
    return _now_ist().time().replace(second=0, microsecond=0)

def _days_to_expiry():
    try:
        exp = datetime.strptime(EXPIRY_DATE, "%d-%m-%Y").date()
        return max((exp - date.today()).days, 1)
    except Exception:
        return 1

def _atm_from_spot(spot):
    return round(spot / STRIKE_STEP) * STRIKE_STEP

def _is_market_hours():
    now = _now_time()
    return time(9, 15) <= now <= time(15, 30)

def _is_weekday():
    return _now_ist().weekday() < 5

def _entry_family(label):
    if label in ("very_bullish", "bullish"):   return "bullish"
    if label in ("very_bearish", "bearish"):   return "bearish"
    return "neutral"


def run():
    log.info("=" * 60)
    log.info(f"Run at {_now_ist().strftime('%Y-%m-%d %H:%M:%S IST')}")

    state = load_state()

    try:
        if not _is_weekday() or not _is_market_hours():
            log.info("Outside trading hours – skipping.")
            return

        today_str = date.today().isoformat()
        now_t     = _now_time()
        dte       = _days_to_expiry()

        # ── Daily reset ────────────────────────────────────────────────────
        if state.get("date") != today_str:
            state = reset_daily_state(state, today_str)
            log.info(f"New trading day: {today_str}")

        if state.get("closed"):
            generate_dashboard(state)
            save_state(state)
            return

        # ── Spot price ─────────────────────────────────────────────────────
        spot = get_spot_price()
        if spot is None:
            msg = "Spot fetch failed – aborting run."
            log.error(msg)
            append_error(state, msg)
            generate_dashboard(state)
            save_state(state)
            return

        # ── Fix ATM ────────────────────────────────────────────────────────
        if state.get("atm") is None and now_t >= MARKET_OPEN:
            atm_price = spot if now_t <= time(9, 20) else (get_915_candle_close() or spot)
            state["atm"] = _atm_from_spot(atm_price)
            log.info(f"ATM fixed at {state['atm']}")

        atm = state.get("atm")
        if atm is None:
            generate_dashboard(state)
            save_state(state)
            return

        # ── 3:00 PM hard close ─────────────────────────────────────────────
        if now_t >= CLOSE_TIME:
            if state.get("positions") and not state.get("closed"):
                state["positions"] = close_all_positions(state["positions"])
                state["closed"]    = True
                send_close_alert(total_pnl(state["positions"]), state["positions"])
            generate_dashboard(state)
            save_state(state)
            return

        # ── Compute view & score ───────────────────────────────────────────
        raw_label, score, direction, snapshot = compute_view(atm)
        state["straddle_snapshot"] = {str(k): v for k, v in snapshot.items()}
        state["current_score"]     = score
        state["raw_label"]         = raw_label

        confirmed_label, label_changed = update_view_state(state, raw_label)
        confirmed_score = score if confirmed_label else None

        # ── View log ───────────────────────────────────────────────────────
        state.setdefault("view_log", []).append({
            "time":      _now_ist().strftime("%H:%M:%S"),
            "label":     raw_label,
            "score":     score,
            "confirmed": confirmed_label or "",
            "pending":   state.get("pending_label") or "",
            "spot":      round(spot, 2)
        })
        state["view_log"] = state["view_log"][-100:]

        # ── 10:30 ENTRY ────────────────────────────────────────────────────
        just_entered = False
        if not state.get("entry_done") and now_t >= ENTRY_TIME:
            entry_label = confirmed_label or raw_label
            legs = build_entry_legs(entry_label, spot, dte)
            if legs:
                state["positions"]         = legs
                state["entry_done"]        = True
                state["entry_label"]       = entry_label
                state["current_label"]     = entry_label
                state["adjustment_count"]  = 0
                state["neutral_adjusted"]  = False
                state["tsl_alerted"]       = False
                state["peak_pnl"]          = 0.0
                state["tsl_level"]         = None
                just_entered               = True
                send_entry_alert(entry_label, legs, spot)
                log.info(f"Entry done: label={entry_label} legs={len(legs)}")
                if now_t > ADD_1200_TIME:
                    state["add_1200_done"] = True
                    log.info("Late start – 12:00 add skipped.")
            else:
                msg = "Entry failed."
                log.error(msg)
                append_error(state, msg)
                send_error_alert(msg)

        # ── Post-entry logic ───────────────────────────────────────────────
        if state.get("entry_done"):

            # MTM refresh
            state["positions"] = update_positions_mtm(state["positions"])
            pnl  = total_pnl(state["positions"])
            log.info(f"MTM P&L: ₹{pnl:,.0f}")

            # Equity curve
            state.setdefault("equity_curve", []).append(
                {"time": _now_ist().strftime("%H:%M:%S"), "pnl": round(pnl, 2)}
            )
            state["equity_curve"] = state["equity_curve"][-100:]

            # Peak P&L tracking
            peak = state.get("peak_pnl", 0.0)
            if pnl > peak:
                state["peak_pnl"] = pnl
                peak = pnl

            # ── Trailing SL check ─────────────────────────────────────────
            if not state.get("tsl_alerted"):
                tsl = compute_tsl(peak)
                if tsl is not None:
                    state["tsl_level"] = tsl
                    if pnl <= tsl:
                        log.info(f"TSL HIT: pnl={pnl:.0f} tsl={tsl:.0f} peak={peak:.0f}")
                        send_tsl_alert(pnl, tsl, peak)
                        state["tsl_alerted"] = True

            # Delta alerts
            breached = check_delta_breaches(
                state["positions"], spot, dte,
                state.get("delta_alerts_sent", [])
            )
            for sym in breached:
                send_delta_alert(sym, 0.35)
                state["delta_alerts_sent"].append(sym)

            adj_count = state.get("adjustment_count", 0)

            # ── Post max-adjustment exit check ────────────────────────────
            if adj_count >= MAX_ADJUSTMENTS and not just_entered:
                if pnl >= POST_ADJ_EXIT_PNL or pnl <= POST_ADJ_EXIT_LOSS:
                    log.info(f"Post-adj exit triggered: pnl={pnl:.0f}")
                    state["positions"] = close_all_positions(state["positions"])
                    state["closed"]    = True
                    send_post_adj_close_alert(pnl, adj_count)
                    generate_dashboard(state)
                    save_state(state)
                    return

            # ── 12:00 one-time addition ───────────────────────────────────
            if (not state.get("add_1200_done")
                    and not just_entered
                    and now_t >= ADD_1200_TIME
                    and adj_count < MAX_ADJUSTMENTS):

                cur_label = confirmed_label or state.get("current_label")
                add_legs  = build_1200_legs(
                    state["entry_label"], cur_label,
                    spot, dte, state["positions"]
                )
                if add_legs:
                    state["positions"].extend(add_legs)
                    state["adjustment_count"] = adj_count + 1
                    send_add_1135_alert(cur_label, add_legs)
                    log.info(f"12:00 add done: {len(add_legs)} legs | adj={adj_count+1}")
                else:
                    log.info("12:00 add: nothing to add.")
                state["add_1200_done"] = True

            # ── Score-threshold adjustment ────────────────────────────────
            elif (confirmed_score is not None
                  and not just_entered
                  and state.get("add_1200_done")   # only after 12pm check done
                  and adj_count < MAX_ADJUSTMENTS):

                entry_label      = state.get("entry_label", "neutral")
                neutral_adjusted = state.get("neutral_adjusted", False)

                if should_adjust(entry_label, confirmed_score, neutral_adjusted):
                    cur_label = confirmed_label
                    old_label = state.get("current_label")
                    log.info(f"Score adj trigger | entry={entry_label} "
                             f"score={confirmed_score:+.1f} → rebalance to {cur_label}")

                    new_legs, closed_syms = build_rebalance_legs(
                        cur_label, spot, dte, state["positions"]
                    )
                    if new_legs:
                        state["positions"].extend(new_legs)

                    if new_legs or closed_syms:
                        state["adjustment_count"] = adj_count + 1
                        if _entry_family(entry_label) == "neutral":
                            state["neutral_adjusted"] = True
                        send_adjustment_alert(old_label, cur_label, new_legs, spot)
                        log.info(f"Adjustment done: "
                                 f"+{len(new_legs)} legs, "
                                 f"{len(closed_syms)} closed | "
                                 f"adj={adj_count+1}")
                    else:
                        log.info("Adjustment triggered but no change needed.")

            # ── Periodic status ───────────────────────────────────────────
            if _now_ist().minute % 30 == 0:
                send_status(state.get("current_label", "—"), spot, pnl)

        # ── Save & dashboard ───────────────────────────────────────────────
        generate_dashboard(state)
        save_state(state)
        log.info("Run complete.")

    except Exception as e:
        log.exception(f"Unhandled error: {e}")
        append_error(state, str(e))
        send_error_alert(str(e))
        try:
            generate_dashboard(state)
            save_state(state)
        except Exception as e2:
            log.error(f"Save failed after error: {e2}")
