"""
telegram_bot.py
Telegram alerts for all strategy events.
"""

import logging
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

log = logging.getLogger(__name__)
API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

VIEW_EMOJI = {
    "very_bullish": "🟢🟢", "bullish": "🟢",
    "neutral": "🟡",
    "bearish": "🔴", "very_bearish": "🔴🔴"
}

def _send(text: str, parse_mode: str = "HTML") -> bool:
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            log.warning("Telegram credentials not set.")
            return False
        r = requests.post(API_URL, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode
        }, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False


def send_entry_alert(label: str, legs: list, spot: float):
    sells = [l for l in legs if l["action"] == "SELL"]
    lines = "\n".join(
        f"  {l['opt_type']} {l['strike']} x{l['lots']}L @ Rs{l['entry_price']:.1f}"
        for l in sells
    )
    emoji = VIEW_EMOJI.get(label, "⚪")
    _send(f"{emoji} <b>ENTRY</b> | <b>{label.upper()}</b>\nSpot: {spot:.0f}\n{lines}")


def send_raw_view_change(old_label: str, new_label: str,
                         score: float, spot: float, pending_count: int):
    """Alert on every raw label change (unconfirmed, 1st bar)."""
    old_label = (old_label or "none").upper()
    new_label = (new_label or "none")
    emoji = VIEW_EMOJI.get(new_label, "⚪")
    lines = [
        f"{emoji} <b>VIEW SHIFT</b> (raw, {pending_count}/2 bars)",
        f"{old_label} to <b>{new_label.upper()}</b>",
        f"Score: <code>{score:+.1f}</code> | Spot: {spot:.0f}",
        "<i>Awaiting confirmation...</i>"
    ]
    _send("\n".join(lines))


def send_confirmed_view_change(old_label: str, new_label: str,
                                score: float, spot: float):
    """Alert when 2-bar confirmation completes."""
    old_label = (old_label or "none").upper()
    new_label = (new_label or "none")
    emoji = VIEW_EMOJI.get(new_label, "⚪")
    lines = [
        f"{emoji} <b>VIEW CONFIRMED</b> ✅",
        f"{old_label} to <b>{new_label.upper()}</b>",
        f"Score: <code>{score:+.1f}</code> | Spot: {spot:.0f}"
    ]
    _send("\n".join(lines))


def send_adjustment_alert(old_label: str, new_label: str,
                          legs: list, spot: float):
    sells = [l for l in legs if l["action"] == "SELL"]
    lines = "\n".join(
        f"  {l['opt_type']} {l['strike']} x{l['lots']}L @ Rs{l['entry_price']:.1f}"
        for l in sells
    )
    _send(f"🔄 <b>ADJUSTMENT</b> | {old_label.upper()} to {new_label.upper()}\nSpot: {spot:.0f}\n{lines}")


def send_add_1135_alert(label: str, legs: list):
    sells = [l for l in legs if l["action"] == "SELL"]
    lines = "\n".join(
        f"  {l['opt_type']} {l['strike']} x{l['lots']}L @ Rs{l['entry_price']:.1f}"
        for l in sells
    )
    _send(f"➕ <b>12:00 ADD</b> | {label.upper()}\n{lines}")


def send_delta_alert(symbol: str, delta: float):
    _send(
        f"⚠️ <b>DELTA BREACH</b>\n"
        f"Symbol: {symbol}\n"
        f"Delta: {delta:.3f} &gt;= 0.35\n"
        f"Please review position."
    )


def send_close_alert(pnl: float, legs: list):
    _send(
        f"🔴 <b>POSITIONS CLOSED @ 3:00 PM</b>\n"
        f"Total P&amp;L: Rs{pnl:,.0f}\n"
        f"Legs closed: {len(legs)}"
    )


def send_error_alert(error_msg: str):
    _send(f"❌ <b>ERROR</b>\n<code>{error_msg[:400]}</code>")


def send_status(label: str, spot: float, pnl: float):
    emoji = VIEW_EMOJI.get(label, "⚪")
    _send(f"📊 <b>STATUS</b> | {emoji} {label.upper()}\nSpot: {spot:.0f} | P&amp;L: Rs{pnl:,.0f}")


def send_tsl_alert(pnl: float, tsl: float, peak: float):
    _send(
        f"⚠️ <b>TRAILING SL HIT</b>\n"
        f"Current P&amp;L: Rs{pnl:,.0f}\n"
        f"Trail SL Level: Rs{tsl:,.0f}\n"
        f"Peak P&amp;L was: Rs{peak:,.0f}\n"
        f"<i>Paper trade - no position closed.</i>"
    )


def send_post_adj_close_alert(pnl: float, adj_count: int):
    direction = "PROFIT" if pnl >= 0 else "LOSS"
    _send(
        f"🔒 <b>POST-ADJUSTMENT EXIT</b>\n"
        f"Reason: {direction} target hit after {adj_count} adjustments\n"
        f"P&amp;L: Rs{pnl:,.0f}\n"
        f"All positions closed."
    )
