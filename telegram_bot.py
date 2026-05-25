"""
telegram_bot.py
Sends trade alerts and status updates via Telegram Bot API.
All calls are fire-and-forget with error logging.
"""

import logging
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"


def _send(text: str, parse_mode: str = "HTML") -> bool:
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            log.warning("Telegram credentials not set – skipping send.")
            return False
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       text,
            "parse_mode": parse_mode
        }
        r = requests.post(API_URL, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False


# ── Specific alert types ───────────────────────────────────────────────────

def send_entry_alert(view: str, legs: list[dict], spot: float):
    sells = [l for l in legs if l["action"] == "SELL"]
    lines = [f"  {l['opt_type']} {l['strike']} x{l['lots']}L @ ₹{l['entry_price']:.1f}"
             for l in sells]
    msg = (
        f"🟢 <b>ENTRY</b> | View: <b>{view.upper()}</b>\n"
        f"Spot: {spot:.0f}\n"
        + "\n".join(lines)
    )
    _send(msg)


def send_adjustment_alert(old_view: str, new_view: str,
                          legs: list[dict], spot: float):
    sells = [l for l in legs if l["action"] == "SELL"]
    lines = [f"  {l['opt_type']} {l['strike']} x{l['lots']}L @ ₹{l['entry_price']:.1f}"
             for l in sells]
    msg = (
        f"🔄 <b>ADJUSTMENT</b> | {old_view.upper()} → {new_view.upper()}\n"
        f"Spot: {spot:.0f}\n"
        + "\n".join(lines)
    )
    _send(msg)


def send_add_1135_alert(view: str, legs: list[dict]):
    sells = [l for l in legs if l["action"] == "SELL"]
    lines = [f"  {l['opt_type']} {l['strike']} x{l['lots']}L @ ₹{l['entry_price']:.1f}"
             for l in sells]
    msg = (
        f"➕ <b>11:35 ADD</b> | View unchanged: {view.upper()}\n"
        + "\n".join(lines)
    )
    _send(msg)


def send_delta_alert(symbol: str, delta: float):
    msg = (
        f"⚠️ <b>DELTA BREACH</b>\n"
        f"Symbol: {symbol}\n"
        f"Delta: {delta:.3f} ≥ 0.35\n"
        f"Please review position."
    )
    _send(msg)


def send_close_alert(total_pnl: float, legs: list[dict]):
    msg = (
        f"🔴 <b>POSITIONS CLOSED @ 3:00 PM</b>\n"
        f"Total P&amp;L: ₹{total_pnl:,.0f}\n"
        f"Legs closed: {len(legs)}"
    )
    _send(msg)


def send_error_alert(error_msg: str):
    msg = f"❌ <b>ERROR</b>\n<code>{error_msg[:400]}</code>"
    _send(msg)


def send_status(view: str, spot: float, pnl: float):
    msg = (
        f"📊 <b>STATUS</b> | {view.upper()}\n"
        f"Spot: {spot:.0f} | P&amp;L: ₹{pnl:,.0f}"
    )
    _send(msg)


def send_tsl_alert(pnl: float, tsl: float, peak: float):
    msg = (
        f"⚠️ <b>TRAILING SL HIT</b>\n"
        f"Current P&amp;L: &#8377;{pnl:,.0f}\n"
        f"Trail SL Level: &#8377;{tsl:,.0f}\n"
        f"Peak P&amp;L was: &#8377;{peak:,.0f}\n"
        f"<i>Paper trade – no position closed.</i>"
    )
    _send(msg)


def send_post_adj_close_alert(pnl: float, adj_count: int):
    direction = "PROFIT" if pnl >= 0 else "LOSS"
    msg = (
        f"🔒 <b>POST-ADJUSTMENT EXIT</b>\n"
        f"Reason: {direction} target hit after {adj_count} adjustments\n"
        f"P&amp;L: &#8377;{pnl:,.0f}\n"
        f"All positions closed."
    )
    _send(msg)
