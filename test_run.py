"""
test_run.py
Runs full strategy logic outside market hours using mock data.
Bypasses: market hours check, Fyers candle fetch, Fyers spot fetch.
Uses: real Fyers auth check, real BS delta, real Telegram, real dashboard.

Trigger: python test_run.py
Or via GitHub Actions: workflow_dispatch on test.yml
"""

import logging
import sys
import os
from datetime import datetime, date, time

import pytz

# ── Logging ────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(f"logs/{date.today()}_test.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger("test_run")

# ── Mock config ────────────────────────────────────────────────────────────
MOCK_SPOT      = 24000.0          # simulated spot price
MOCK_ATM       = 24000            # ATM = nearest 100 multiple of spot
MOCK_DTE       = 3                # days to expiry

# Simulated VWAP scenario — set your desired test view here:
# very_bullish: ATM below + all upper below + all lower above
# bearish:      all upper above + all lower below
# neutral:      mixed
MOCK_SCENARIO  = os.environ.get("TEST_SCENARIO", "bullish")   # override via env

SCENARIOS = {
    "very_bullish": dict(atm_below=True,  upper_below=4, upper_above=0, lower_above=4, lower_below=0),
    "bullish":      dict(atm_below=True,  upper_below=3, upper_above=1, lower_above=3, lower_below=1),
    "neutral":      dict(atm_below=True,  upper_below=1, upper_above=3, lower_above=1, lower_below=3),
    "bearish":      dict(atm_below=False, upper_below=0, upper_above=4, lower_above=0, lower_below=4),
    "very_bearish": dict(atm_below=False, upper_below=0, upper_above=4, lower_above=0, lower_below=4),
}


def _mock_snapshot(atm: int, scenario: dict) -> dict:
    """Build a fake straddle snapshot matching the scenario."""
    from config import STRIKE_STEP, STRADDLE_RANGE
    strikes = [atm + i * STRIKE_STEP for i in range(-STRADDLE_RANGE, STRADDLE_RANGE + 1)]
    upper   = [atm + i * STRIKE_STEP for i in range(1, STRADDLE_RANGE + 1)]
    lower   = [atm - i * STRIKE_STEP for i in range(1, STRADDLE_RANGE + 1)]

    snapshot = {}
    # ATM
    snapshot[atm] = {
        "vwap": 200.0, "current_price": 190.0 if scenario["atm_below"] else 210.0,
        "below_vwap": scenario["atm_below"], "above_vwap": not scenario["atm_below"]
    }
    # Upper strikes
    ub = scenario["upper_below"]
    for i, s in enumerate(upper):
        below = i < ub
        snapshot[s] = {
            "vwap": 150.0 - i * 10, "current_price": 140.0 - i * 10 if below else 160.0 - i * 10,
            "below_vwap": below, "above_vwap": not below
        }
    # Lower strikes (inverted: above_vwap = bullish)
    la = scenario["lower_above"]
    for i, s in enumerate(lower):
        above = i < la
        snapshot[s] = {
            "vwap": 150.0 - i * 10, "current_price": 160.0 - i * 10 if above else 140.0 - i * 10,
            "below_vwap": not above, "above_vwap": above
        }
    return {str(k): v for k, v in snapshot.items()}


def _mock_option_quote(strike: int, opt_type: str, spot: float) -> dict:
    """Return realistic mock LTP and delta for a strike."""
    from fyers_data import bs_delta, _option_symbol
    delta = bs_delta(spot, strike, opt_type, MOCK_DTE)
    # Simple mock LTP: distance-based decay
    dist  = abs(strike - spot)
    ltp   = max(round(max(30 - dist * 0.05, 2.5), 1), 2.5)
    return {
        "symbol":       _option_symbol(strike, opt_type),
        "strike":       strike,
        "opt_type":     opt_type,
        "ltp":          ltp,
        "delta":        delta,
        "greeks_source":"bs"
    }


def run_test():
    log.info("=" * 60)
    log.info(f"TEST RUN at {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S IST')}")
    log.info(f"Scenario: {MOCK_SCENARIO} | Spot: {MOCK_SPOT} | ATM: {MOCK_ATM}")

    # ── Patch fyers_data functions with mocks ──────────────────────────────
    import fyers_data
    import position_manager
    from config import STRIKE_STEP

    # Mock get_spot_price
    fyers_data.get_spot_price = lambda: MOCK_SPOT

    # Mock get_915_candle_close
    fyers_data.get_915_candle_close = lambda: MOCK_SPOT

    # Mock get_straddle_candles — return simple rising/flat candle list
    def _mock_straddle_candles(strike):
        base = 200 - abs(strike - MOCK_ATM) * 0.1
        return [
            {"ts": 1000 + i * 300, "open": base, "high": base + 2,
             "low": base - 2, "close": base - i * 0.5, "volume": 1000 + i * 100}
            for i in range(10)
        ]
    fyers_data.get_straddle_candles = _mock_straddle_candles

    # Mock get_quotes_batch — return realistic quotes for any symbols
    def _mock_batch(symbols):
        result = {}
        for sym in symbols:
            # Parse strike and type from symbol e.g. NSE:NIFTY26MAY2624000CE
            try:
                tail   = sym.split("NIFTY")[1][6:]     # strip expiry prefix
                otype  = tail[-2:]
                strike = int(tail[:-2])
            except Exception:
                strike, otype = MOCK_ATM, "CE"
            q = _mock_option_quote(strike, otype, MOCK_SPOT)
            result[sym] = q
        return result
    fyers_data.get_quotes_batch = _mock_batch

    # Mock compute_view to use our scenario snapshot
    import view_engine
    scenario     = SCENARIOS.get(MOCK_SCENARIO, SCENARIOS["bullish"])
    mock_snap    = _mock_snapshot(MOCK_ATM, scenario)

    _orig_compute = view_engine.compute_view
    def _mock_compute_view(atm):
        from view_engine import compute_score, score_to_label, label_to_direction
        s = scenario
        score     = compute_score(s["atm_below"], s["upper_below"], s["upper_above"],
                                  s["lower_above"], s["lower_below"])
        label     = score_to_label(score)
        direction = label_to_direction(label)
        log.info(f"[MOCK] compute_view | score={score:+.1f} label={label}")
        return label, score, direction, {int(k): v for k, v in mock_snap.items()}
    view_engine.compute_view = _mock_compute_view

    # ── Now run real strategy with mocked data ─────────────────────────────
    from state_manager import load_state, save_state, reset_daily_state
    import json

    # Force fresh state for test
    state = reset_daily_state({}, date.today().isoformat())
    state["atm"]   = MOCK_ATM
    # Simulate we're at 10:30 so entry fires
    state["_test"] = True

    # Patch _now_time in strategy to return 10:30
    import strategy
    strategy._now_time = lambda: time(10, 30)
    strategy._is_market_hours = lambda: True
    strategy._is_weekday      = lambda: True
    strategy._days_to_expiry  = lambda: MOCK_DTE

    # Save state so strategy.run() picks it up
    save_state(state)

    # Run strategy
    strategy.run()

    # Show final state summary
    final = load_state()
    log.info("=" * 60)
    log.info("TEST COMPLETE — Final state summary:")
    log.info(f"  Entry done:    {final.get('entry_done')}")
    log.info(f"  Entry label:   {final.get('entry_label')}")
    log.info(f"  Score:         {final.get('current_score')}")
    log.info(f"  Positions:     {len(final.get('positions', []))} legs")
    log.info(f"  Adj count:     {final.get('adjustment_count')}")
    log.info(f"  Dashboard:     docs/index.html")

    for p in final.get("positions", []):
        if p["action"] == "SELL":
            log.info(f"  SELL {p['opt_type']} {p['strike']} "
                     f"x{p['lots']}L @ ₹{p['entry_price']} "
                     f"delta={p.get('entry_delta','?')} "
                     f"time={p.get('entry_time','?')}")

    log.info("=" * 60)
    log.info("Check Telegram for entry alert and docs/index.html for dashboard.")


if __name__ == "__main__":
    try:
        run_test()
        sys.exit(0)
    except Exception as e:
        log.exception(f"Test run failed: {e}")
        sys.exit(1)
