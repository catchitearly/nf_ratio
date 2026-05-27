"""
journal.py
Generates a full-day trading journal as a markdown file.
Saved to journal/YYYY-MM-DD.md at end of day (3pm close).
Covers: summary, score timeline, position log, trade history, P&L, errors.
"""

import logging
import os
from datetime import datetime

log = logging.getLogger(__name__)

JOURNAL_DIR = "journal"

VIEW_EMOJI = {
    "very_bullish": "🟢🟢", "bullish": "🟢",
    "neutral": "🟡",
    "bearish": "🔴", "very_bearish": "🔴🔴"
}


def _fmt(iso):
    if not iso: return "—"
    try:
        s = str(iso)
        if "T" in s: return s.replace("T", " ")[:19]
        return s[:19]
    except: return str(iso)


def _pnl_arrow(v):
    return "▲" if v >= 0 else "▼"


def generate_journal(state: dict):
    """Write full day journal to journal/YYYY-MM-DD.md"""
    try:
        os.makedirs(JOURNAL_DIR, exist_ok=True)
        today      = state.get("date") or datetime.now().strftime("%Y-%m-%d")
        path       = os.path.join(JOURNAL_DIR, f"{today}.md")
        generated  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        positions  = state.get("positions", [])
        view_log   = state.get("view_log", [])
        equity     = state.get("equity_curve", [])
        errors     = state.get("errors", [])
        entry_label = state.get("entry_label", "—")
        atm         = state.get("atm", "—")
        adj_count   = state.get("adjustment_count", 0)
        peak_pnl    = state.get("peak_pnl", 0.0)
        tsl_level   = state.get("tsl_level")
        tsl_hit     = state.get("tsl_alerted", False)
        add_done    = state.get("add_1200_done", False)

        # Final P&L
        final_pnl  = sum(p.get("pnl", 0) for p in positions)
        pnl_arrow  = _pnl_arrow(final_pnl)

        # Sell legs only for summary
        sell_legs  = [p for p in positions if p.get("action") == "SELL"]
        closed_legs = [p for p in positions if p.get("closed")]

        lines = []

        # ── Header ────────────────────────────────────────────────────────
        lines.append(f"# 📓 Trading Journal — {today}")
        lines.append(f"*Generated: {generated} IST*")
        lines.append("")

        # ── Day Summary ───────────────────────────────────────────────────
        lines.append("## 📊 Day Summary")
        lines.append("")
        lines.append(f"| Field | Value |")
        lines.append(f"|---|---|")
        lines.append(f"| Date | {today} |")
        lines.append(f"| ATM Strike | {atm} |")
        lines.append(f"| Entry View | {entry_label.upper()} {VIEW_EMOJI.get(entry_label,'')} |")
        lines.append(f"| Final View | {state.get('current_label','—').upper()} {VIEW_EMOJI.get(state.get('current_label',''),'').strip()} |")
        lines.append(f"| Final P&L | {pnl_arrow} ₹{final_pnl:,.0f} |")
        lines.append(f"| Peak P&L | ₹{peak_pnl:,.0f} |")
        lines.append(f"| TSL Level | {'₹'+str(int(tsl_level)) if tsl_level else 'Not activated'} |")
        lines.append(f"| TSL Hit | {'Yes ⚠️' if tsl_hit else 'No'} |")
        lines.append(f"| Total Adjustments | {adj_count} / 4 |")
        lines.append(f"| 12:00 Add Done | {'Yes' if add_done else 'No'} |")
        lines.append(f"| Total Legs | {len(positions)} ({len(sell_legs)} sell, {len(positions)-len(sell_legs)} hedge) |")
        lines.append(f"| Legs Closed | {len(closed_legs)} |")
        lines.append("")

        # ── Equity Curve Summary ──────────────────────────────────────────
        lines.append("## 📈 Equity Curve")
        lines.append("")
        if equity:
            pnls = [e["pnl"] for e in equity]
            max_pnl = max(pnls)
            min_pnl = min(pnls)
            # Max drawdown
            dd = 0.0
            for i, v in enumerate(pnls):
                pk = max(pnls[:i+1])
                dd = min(dd, v - pk)
            lines.append(f"| Metric | Value |")
            lines.append(f"|---|---|")
            lines.append(f"| Opening P&L | ₹{pnls[0]:,.0f} |")
            lines.append(f"| Peak P&L | ₹{max_pnl:,.0f} |")
            lines.append(f"| Trough P&L | ₹{min_pnl:,.0f} |")
            lines.append(f"| Max Drawdown | ₹{dd:,.0f} |")
            lines.append(f"| Closing P&L | ₹{pnls[-1]:,.0f} |")
            lines.append(f"| Data Points | {len(equity)} (5-min intervals) |")
            lines.append("")
            lines.append("### P&L Timeline")
            lines.append("")
            lines.append("| Time | P&L | Change |")
            lines.append("|---|---|---|")
            prev = 0.0
            for e in equity:
                chg  = e["pnl"] - prev
                arrow = "▲" if chg >= 0 else "▼"
                lines.append(f"| {e['time']} | ₹{e['pnl']:,.0f} | {arrow} ₹{abs(chg):,.0f} |")
                prev = e["pnl"]
        else:
            lines.append("*No equity data recorded.*")
        lines.append("")

        # ── Score / View Log ──────────────────────────────────────────────
        lines.append("## 🧭 Score & View Timeline")
        lines.append("")
        if view_log:
            lines.append("| Time | Label | Score | Confirmed | Pending | Spot |")
            lines.append("|---|---|---|---|---|---|")
            prev_label = None
            for e in view_log:
                v         = e.get("label") or e.get("view", "")
                score_val = e.get("score")
                score_str = f"{score_val:+.1f}" if score_val is not None else "—"
                confirmed = e.get("confirmed", "")
                pending   = e.get("pending", "")
                spot_val  = e.get("spot", "—")
                changed   = v != prev_label and prev_label is not None
                marker    = " ⟳" if changed else ""
                emoji     = VIEW_EMOJI.get(v, "")
                lines.append(
                    f"| {e.get('time','—')} | {emoji} {v.upper()}{marker} | "
                    f"`{score_str}` | {confirmed.upper() if confirmed else '—'} | "
                    f"{pending.upper() if pending else '—'} | ₹{spot_val} |"
                )
                prev_label = v
        else:
            lines.append("*No view data recorded.*")
        lines.append("")

        # ── Position Log ──────────────────────────────────────────────────
        lines.append("## 📋 Position Log")
        lines.append("")
        if positions:
            lines.append("| Status | Type | Strike | Action | Lots | Entry Δ | Cur Δ | Entry ₹ | LTP ₹ | Entry Time | Close Time | P&L |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
            for p in positions:
                st        = "✓ Closed" if p.get("closed") else "● Open"
                opt       = p.get("opt_type", "")
                strike    = p.get("strike", "")
                action    = p.get("action", "")
                lots      = p.get("lots", "")
                entry_d   = p.get("entry_delta", 0.0)
                cur_d     = p.get("current_delta", 0.0)
                ep        = p.get("entry_price", 0)
                ltp       = p.get("current_ltp", 0)
                etime     = str(p.get("entry_time", "—"))[:8]
                ctime     = str(p.get("close_time", "—"))
                if "T" in ctime: ctime = ctime.split("T")[1][:8]
                pnl       = p.get("pnl", 0)
                pnl_a     = _pnl_arrow(pnl)
                lines.append(
                    f"| {st} | {opt} | {strike} | {action} | {lots} | "
                    f"{entry_d:.3f} | {cur_d:.3f} | ₹{ep:.1f} | ₹{ltp:.1f} | "
                    f"{etime} | {ctime[:8]} | {pnl_a} ₹{abs(pnl):,.0f} |"
                )
        else:
            lines.append("*No positions taken today.*")
        lines.append("")

        # ── Adjustment Log ────────────────────────────────────────────────
        lines.append("## 🔄 Adjustment Log")
        lines.append("")
        adj_legs = [p for p in positions
                    if p.get("tag", "").startswith(("sell_1200","sell_reb","sell_adj"))]
        if adj_legs:
            lines.append("| Entry Time | Type | Strike | Action | Lots | Tag | Entry ₹ | P&L |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for p in adj_legs:
                pnl   = p.get("pnl", 0)
                etime = str(p.get("entry_time","—"))[:8]
                lines.append(
                    f"| {etime} | {p.get('opt_type','')} | {p.get('strike','')} | "
                    f"{p.get('action','')} | {p.get('lots','')} | {p.get('tag','')} | "
                    f"₹{p.get('entry_price',0):.1f} | {_pnl_arrow(pnl)} ₹{abs(pnl):,.0f} |"
                )
        else:
            lines.append("*No adjustments made today.*")
        lines.append("")

        # ── Error Log ─────────────────────────────────────────────────────
        lines.append("## 🔴 Error Log")
        lines.append("")
        if errors:
            lines.append("| Time | Error |")
            lines.append("|---|---|")
            for e in errors:
                lines.append(f"| {_fmt(e.get('time'))} | {e.get('msg','')[:200]} |")
        else:
            lines.append("*No errors recorded. ✅*")
        lines.append("")

        # ── Footer ────────────────────────────────────────────────────────
        lines.append("---")
        lines.append(f"*Journal auto-generated by Nifty Options Paper Trader at {generated} IST*")

        content = "\n".join(lines)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        log.info(f"Journal saved: {path} ({len(content)} bytes)")
        return path

    except Exception as e:
        log.error(f"generate_journal error: {e}")
        return None
