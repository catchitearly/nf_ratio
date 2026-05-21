"""
strategy.py
Main orchestrator – called every 5 minutes by main.py.
Handles: ATM setup, entry, view monitoring, adjustments, close.
All logic is stateless between runs; full state loaded from state.json.
"""

import logging
from datetime import datetime, date, time, timedelta

import pytz

from config import (
    ENTRY_TIME, CLOSE_TIME, ADD_QTY_DEADLINE, MARKET_OPEN,
    EXPIRY_DATE, STRIKE_STEP, PAPER_TRADE
)
from state_manager import load_state, save_state, reset_daily_state, append_error
from fyers_data import get_spot_price, get_915_candle_close
from view_engine import compute_view, update_view_state, VIEW_BULL, VIEW_BEAR, VIEW_NEUTRAL
from position_manager import (
    build_entry_legs, build_adjustment_legs, build_add_1135_legs,
    update_positions_mtm, close_all_positions,
    check_delta_breaches, total_pnl
)
from telegram_bot import (
    send_entry_alert, send_adjustment_alert, send_add_1135_alert,
    send_delta_alert, send_close_alert, send_error_alert, send_status
)
from dashboard_gen import generate_dashboard

log = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


# ── Helpers ────────────────────────────────────────────────────────────────

def _now_ist() -> datetime:
    return datetime.now(IST)


def _now_time() -> time:
    return _now_ist().time().replace(second=0, microsecond=0)


def _days_to_expiry() -> float:
    """Calendar days to expiry (minimum 1 day to avoid zero-div)."""
    try:
        exp = datetime.strptime(EXPIRY_DATE, "%d-%m-%Y").date()
        delta = (exp - date.today()).days
        return max(delta, 1)
    except Exception:
        return 1


def _atm_from_spot(spot: float) -> int:
    """Round spot to nearest 100 multiple."""
    return round(spot / STRIKE_STEP) * STRIKE_STEP


def _is_market_hours() -> bool:
    now = _now_time()
    return time(9, 15) <= now <= time(15, 30)


def _is_weekday() -> bool:
    return _now_ist().weekday() < 5


# ── Main run ───────────────────────────────────────────────────────────────

def run():
    log.info("=" * 60)
    log.info(f"Strategy run at {_now_ist().strftime('%Y-%m-%d %H:%M:%S IST')}")

    state = load_state()

    try:
        if not _is_weekday():
            log.info("Weekend – skipping run.")
            return

        if not _is_market_hours():
            log.info("Outside market hours – skipping run.")
            return

        today_str = date.today().isoformat()
        now_t     = _now_time()
        dte       = _days_to_expiry()

        # ── Daily reset ────────────────────────────────────────────────────
        if state.get("date") != today_str:
            state = reset_daily_state(state, today_str)
            log.info(f"New trading day: {today_str}")

        # ── If already closed today, just regenerate dashboard ─────────────
        if state.get("closed"):
            log.info("Positions already closed for today.")
            generate_dashboard(state)
            save_state(state)
            return

        # ── Fetch spot ─────────────────────────────────────────────────────
        spot = get_spot_price()
        if spot is None:
            msg = "Could not fetch spot price – aborting this run."
            log.error(msg)
            append_error(state, msg)
            generate_dashboard(state)
            save_state(state)
            return
        log.info(f"Spot price: {spot:.2f}")

        # ── Fix ATM from 9:15 candle (done once per day) ───────────────────
        if state.get("atm") is None and now_t >= MARKET_OPEN:
            if now_t <= time(9, 20):
                # Within first 5 min: use live spot (9:15 candle not yet closed)
                atm_price = spot
                log.info(f"ATM: using live spot at 9:15 open ({spot:.2f})")
            else:
                # After 9:20: fetch actual 9:15 candle close from history API
                atm_price = get_915_candle_close()
                if atm_price is None:
                    log.warning("9:15 candle fetch failed – falling back to current spot.")
                    atm_price = spot
                else:
                    log.info(f"ATM: using 9:15 candle close ({atm_price:.2f})")
            atm = _atm_from_spot(atm_price)
            state["atm"] = atm
            log.info(f"ATM fixed at {atm}")

        atm = state.get("atm")
        if atm is None:
            log.info("ATM not yet set (before 9:15) – skipping.")
            generate_dashboard(state)
            save_state(state)
            return

        # ── 3:00 PM CLOSE ──────────────────────────────────────────────────
        if now_t >= CLOSE_TIME:
            if state.get("positions") and not state.get("closed"):
                log.info("3:00 PM – closing all positions.")
                state["positions"] = close_all_positions(state["positions"])
                state["closed"]    = True
                pnl = total_pnl(state["positions"])
                send_close_alert(pnl, state["positions"])
            generate_dashboard(state)
            save_state(state)
            return

        # ── Compute view ───────────────────────────────────────────────────
        raw_view, snapshot = compute_view(atm)
        state["straddle_snapshot"] = {str(k): v for k, v in snapshot.items()}
        log.info(f"Raw view this tick: {raw_view}")

        # View confirmation logic
        confirmed_view, view_changed = update_view_state(state, raw_view)

        # ── 10:30 ENTRY ────────────────────────────────────────────────────
        just_entered = False
        if not state.get("entry_done") and now_t >= ENTRY_TIME:
            entry_view = confirmed_view or raw_view   # use best available view
            log.info(f"10:30 entry trigger | view={entry_view}")
            legs = build_entry_legs(entry_view, spot, dte)
            if legs:
                state["positions"]    = legs
                state["entry_done"]   = True
                state["entry_view"]   = entry_view
                state["current_view"] = entry_view
                just_entered          = True   # ← guard: skip 11:35 add this same run
                send_entry_alert(entry_view, legs, spot)
                log.info(f"Entry executed: {len(legs)} legs | view={entry_view}")
                # If we're starting late (after 11:35), mark add as already done
                # so it doesn't fire on next run either — window has passed
                if now_t > ADD_QTY_DEADLINE:
                    state["add_1135_done"] = True
                    log.info("Late start after 11:35 – 11:35 add window skipped.")
            else:
                msg = "Entry failed – could not build legs."
                log.error(msg)
                append_error(state, msg)
                send_error_alert(msg)

        # ── Post-entry logic ───────────────────────────────────────────────
        if state.get("entry_done"):

            # MTM update
            state["positions"] = update_positions_mtm(state["positions"])
            pnl = total_pnl(state["positions"])
            log.info(f"MTM P&L: ₹{pnl:,.0f}")

            # Delta breach alerts
            breached = check_delta_breaches(
                state["positions"], spot, dte,
                state.get("delta_alerts_sent", [])
            )
            for sym in breached:
                send_delta_alert(sym, 0.35)   # exact delta logged separately
                state["delta_alerts_sent"].append(sym)

            # ── View change adjustment ────────────────────────────────────
            if view_changed and confirmed_view:
                old_view = state.get("prev_view", state.get("entry_view"))
                log.info(f"View changed {old_view}→{confirmed_view} – adding legs.")
                adj_legs = build_adjustment_legs(
                    confirmed_view, old_view, spot, dte
                )
                if adj_legs:
                    state["positions"].extend(adj_legs)
                    send_adjustment_alert(old_view, confirmed_view, adj_legs, spot)
                else:
                    msg = f"Adjustment legs failed ({old_view}→{confirmed_view})."
                    log.error(msg)
                    append_error(state, msg)

            # ── 11:35 one-time add (view unchanged since entry) ───────────
            if (
                not state.get("add_1135_done")
                and not just_entered                              # ← never same run as entry
                and now_t >= ADD_QTY_DEADLINE
                and state.get("current_view") == state.get("entry_view")
            ):
                log.info("11:35 one-time add trigger.")
                add_legs = build_add_1135_legs(state["entry_view"], spot, dte)
                if add_legs:
                    state["positions"].extend(add_legs)
                    state["add_1135_done"] = True
                    send_add_1135_alert(state["entry_view"], add_legs)
                else:
                    msg = "11:35 add legs failed."
                    log.error(msg)
                    append_error(state, msg)

            # ── Periodic status update (every ~30 min) ────────────────────
            if _now_ist().minute % 30 == 0:
                send_status(state.get("current_view", "—"), spot, pnl)

        # ── Save & render dashboard ────────────────────────────────────────
        generate_dashboard(state)
        save_state(state)
        log.info("Run complete.")

    except Exception as e:
        log.exception(f"Unhandled exception in strategy.run(): {e}")
        append_error(state, str(e))
        send_error_alert(str(e))
        try:
            generate_dashboard(state)
            save_state(state)
        except Exception as e2:
            log.error(f"Failed to save state after error: {e2}")
