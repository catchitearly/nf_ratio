"""
dashboard_gen.py — 3-tab dashboard: Overview | View Log | Equity Curve
"""

import json, logging, os
from datetime import datetime
from config import DASHBOARD_FILE
from position_manager import total_pnl

log = logging.getLogger(__name__)

VIEW_COLOR = {
    "very_bullish": "#00e5a0", "bullish": "#00c896",
    "neutral":      "#f0b429",
    "bearish":      "#ff4e6a", "very_bearish": "#cc2244",
    None: "#888"
}

def _pnl_color(v):   return "#00c896" if v >= 0 else "#ff4e6a"
def _fmt_time(iso):
    if not iso: return "&mdash;"
    try:    return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except: return str(iso)

def _view_badge(v):
    col = VIEW_COLOR.get(v, "#888")
    return ("<span style='background:" + col + "20;color:" + col + ";"
            "border:1px solid " + col + "60;padding:2px 10px;"
            "border-radius:12px;font-size:10px;font-weight:700;"
            "text-transform:uppercase;letter-spacing:1px'>"
            + (v or "&mdash;") + "</span>")

def _straddle_rows(snapshot, atm):
    if not snapshot:
        return "<tr><td colspan='4' style='text-align:center;color:var(--muted)'>No straddle data yet</td></tr>"
    rows = ""
    for strike in sorted(snapshot.keys(), key=lambda x: int(x), reverse=True):
        s      = snapshot[strike]
        is_atm = int(strike) == atm
        label  = ("<b style='color:#fff'>" + str(strike) + " &#9733;</b>") if is_atm else str(strike)
        price  = s.get("current_price")
        vwap   = s.get("vwap")
        below  = s.get("below_vwap", False)
        above  = s.get("above_vwap", False)
        rel    = "&#8595; Below" if below else "&#8593; Above" if above else "&mdash;"
        rc     = "#00c896" if below else "#ff4e6a" if above else "#5a6480"
        bg     = "background:#ffffff08;" if is_atm else ""
        rows  += ("<tr style='" + bg + "'><td>" + label + "</td>"
                  "<td>" + (f"{price:.1f}" if price else "&mdash;") + "</td>"
                  "<td>" + (f"{vwap:.1f}"  if vwap  else "&mdash;") + "</td>"
                  "<td style='color:" + rc + ";font-weight:700'>" + rel + "</td></tr>")
    return rows

def _position_rows(positions):
    if not positions:
        return "<tr><td colspan='8' style='text-align:center;color:var(--muted)'>No positions yet</td></tr>"
    rows = ""
    for p in positions:
        pnl  = p.get("pnl", 0)
        pc   = _pnl_color(pnl)
        st   = "<span style='color:#00c896'>&#10003;</span>" if p.get("closed") else "<span style='color:#f0b429'>&#9679;</span>"
        ac   = "#ff4e6a" if p.get("action") == "SELL" else "#4ea8ff"
        tc   = "#38bdf8" if p.get("opt_type") == "CE"  else "#f97316"
        rows += ("<tr><td>" + st + "</td>"
                 "<td style='color:" + tc + ";font-weight:700'>" + str(p.get("opt_type","")) + "</td>"
                 "<td>" + str(p.get("strike","")) + "</td>"
                 "<td style='color:" + ac + ";font-weight:700'>" + str(p.get("action","")) + "</td>"
                 "<td>" + str(p.get("lots","")) + "</td>"
                 "<td>&#8377;" + f"{p.get('entry_price',0):.1f}" + "</td>"
                 "<td>&#8377;" + f"{p.get('current_ltp',0):.1f}" + "</td>"
                 "<td style='color:" + pc + ";font-weight:700'>&#8377;" + f"{pnl:,.0f}" + "</td></tr>")
    return rows

def _error_rows(errors):
    if not errors:
        return "<tr><td colspan='2' style='text-align:center;color:var(--muted)'>No errors</td></tr>"
    rows = ""
    for e in reversed(errors[-10:]):
        rows += ("<tr><td style='white-space:nowrap;color:var(--muted)'>" + _fmt_time(e.get("time")) + "</td>"
                 "<td style='color:#ff4e6a'>" + str(e.get("msg",""))[:150] + "</td></tr>")
    return rows

def _view_log_rows(view_log):
    if not view_log:
        return "<tr><td colspan='6' style='text-align:center;color:var(--muted)'>No view data yet</td></tr>"
    rows = ""
    prev_label = None
    for entry in reversed(view_log):
        v         = entry.get("label") or entry.get("view", "")
        score     = entry.get("score")
        changed   = (v != prev_label) and (prev_label is not None)
        row_bg    = "background:#ffffff06;" if changed else ""
        chg_mark  = "<span style='color:#f0b429;margin-left:6px'>&#8635;</span>" if changed else ""
        confirmed = entry.get("confirmed", "")
        pending   = entry.get("pending", "")
        conf_html = _view_badge(confirmed) if confirmed else "<span style='color:var(--muted)'>&mdash;</span>"
        pend_html = _view_badge(pending)   if pending   else "&mdash;"
        spot_val  = entry.get("spot", "&mdash;")
        time_val  = entry.get("time", "&mdash;")
        score_col = ("#00c896" if score >= 0 else "#ff4e6a") if score is not None else "#5a6480"
        score_str = (f"{score:+.1f}" if score is not None else "&mdash;")
        rows += ("<tr style='" + row_bg + "'>"
                 "<td style='color:var(--muted);white-space:nowrap'>" + str(time_val) + "</td>"
                 "<td>" + _view_badge(v) + chg_mark + "</td>"
                 "<td style='font-family:monospace;font-weight:700;color:" + score_col + "'>" + score_str + "</td>"
                 "<td>" + conf_html + "</td>"
                 "<td>" + pend_html + "</td>"
                 "<td style='font-family:monospace'>&#8377;" + str(spot_val) + "</td>"
                 "</tr>")
        prev_label = v
    return rows


def generate_dashboard(state: dict):
    try:
        os.makedirs(os.path.dirname(DASHBOARD_FILE), exist_ok=True)

        atm          = state.get("atm") or 0
        view         = state.get("current_label") or state.get("current_view") or "neutral"
        pending      = state.get("pending_label") or state.get("pending_view") or "&mdash;"
        entry_view   = state.get("entry_label") or state.get("entry_view") or "&mdash;"
        raw_score    = state.get("current_score")
        score_disp   = (f"{raw_score:+.1f}" if raw_score is not None else "&mdash;")
        add_done     = state.get("add_1200_done") or state.get("add_1145_done") or state.get("add_1135_done", False)
        adj_count    = state.get("adjustment_count", 0)
        peak_pnl     = state.get("peak_pnl", 0.0)
        tsl_level    = state.get("tsl_level")
        tsl_alerted  = state.get("tsl_alerted", False)
        last_run     = _fmt_time(state.get("last_run"))
        positions    = state.get("positions", [])
        errors       = state.get("errors", [])
        entry_done   = state.get("entry_done", False)
        closed       = state.get("closed", False)
        snapshot     = state.get("straddle_snapshot", {})
        view_log     = state.get("view_log", [])
        equity_curve = state.get("equity_curve", [])
        pnl          = total_pnl(positions)
        view_col     = VIEW_COLOR.get(view, "#888")
        pnl_col      = _pnl_color(pnl)

        straddle_html = _straddle_rows(snapshot, atm)
        pos_html      = _position_rows(positions)
        err_html      = _error_rows(errors)
        vlog_html     = _view_log_rows(view_log)

        # Status badges
        sf = ""
        if entry_done:  sf += "<span class='badge green'>ENTRY DONE</span> "
        if add_done:    sf += "<span class='badge blue'>12:00 ADD</span> "
        if tsl_alerted: sf += "<span class='badge yellow'>TSL HIT</span> "
        if closed:      sf += "<span class='badge red'>CLOSED</span> "
        if not sf:      sf  = "<span class='badge yellow'>WAITING</span>"

        view_changes = sum(
            1 for i in range(1, len(view_log))
            if (view_log[i].get("label") or view_log[i].get("view","")) !=
               (view_log[i-1].get("label") or view_log[i-1].get("view",""))
        )

        tsl_display  = ("&#8377;" + str(int(tsl_level))) if tsl_level else "&mdash;"
        tsl_color    = "#ff4e6a" if tsl_alerted else "#5a6480"
        adj_color    = "#f0b429" if adj_count >= 4 else "#fff"

        eq_times_json = json.dumps([e["time"] for e in equity_curve])
        eq_pnls_json  = json.dumps([e["pnl"]  for e in equity_curve])

        # Build view log table HTML as plain string (no f-string ternary)
        if view_log:
            vlog_table = ("<table><thead><tr>"
                          "<th>Time</th><th>Label</th><th>Score</th>"
                          "<th>Confirmed</th><th>Pending</th><th>Spot</th>"
                          "</tr></thead><tbody>" + vlog_html + "</tbody></table>")
        else:
            vlog_table = "<div class='no-data'>No view data yet &mdash; starts after 9:15 AM</div>"

        html_parts = []
        html_parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="300"/>
<title>Nifty Options | Paper Trade</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');
  :root {
    --bg:#0b0e14; --surface:#12161f; --border:#1e2433;
    --text:#c9d1e0; --muted:#5a6480;
    --green:#00c896; --red:#ff4e6a; --yellow:#f0b429; --blue:#4ea8ff; --accent:#7c5cff;
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); font-family:'JetBrains Mono',monospace; font-size:13px; }
  .header { display:flex; align-items:center; justify-content:space-between; padding:16px 24px;
            border-bottom:1px solid var(--border); background:var(--surface); flex-wrap:wrap; gap:8px; }
  .header h1 { font-family:'Syne',sans-serif; font-size:22px; font-weight:800; color:#fff; }
  .header-meta { font-size:11px; color:var(--muted); }
  .tab-bar { display:flex; border-bottom:1px solid var(--border); background:var(--surface); padding:0 24px; }
  .tab { padding:12px 20px; font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.8px;
         color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; transition:all 0.15s; white-space:nowrap; }
  .tab:hover { color:var(--text); }
  .tab.active { color:#fff; border-bottom-color:var(--accent); }
  .tab-badge { background:var(--accent); color:#fff; border-radius:8px; font-size:9px; padding:1px 5px; margin-left:6px; }
  .panel { display:none; padding:20px 24px; }
  .panel.active { display:block; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:20px; }
  .card { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:14px 16px; }
  .card .label { font-size:10px; text-transform:uppercase; letter-spacing:1px; color:var(--muted); margin-bottom:6px; }
  .card .value { font-family:'Syne',sans-serif; font-size:20px; font-weight:700; color:#fff; }
  .card .sub { font-size:10px; color:var(--muted); margin-top:4px; }
  .section { background:var(--surface); border:1px solid var(--border); border-radius:8px; margin-bottom:16px; overflow:hidden; }
  .section-hdr { display:flex; align-items:center; justify-content:space-between;
                 padding:10px 16px; border-bottom:1px solid var(--border); background:#0f1219; }
  .section-hdr h2 { font-family:'Syne',sans-serif; font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:1px; color:#fff; }
  table { width:100%; border-collapse:collapse; }
  th { padding:8px 12px; text-align:left; font-size:10px; text-transform:uppercase;
       letter-spacing:0.8px; color:var(--muted); border-bottom:1px solid var(--border); background:#0f1219; }
  td { padding:8px 12px; border-bottom:1px solid #161b26; vertical-align:middle; }
  tr:last-child td { border-bottom:none; }
  tr:hover td { background:#161b2688; }
  .badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; margin-right:4px; }
  .badge.green  { background:#00c89620; color:var(--green); border:1px solid #00c89640; }
  .badge.red    { background:#ff4e6a20; color:var(--red);   border:1px solid #ff4e6a40; }
  .badge.blue   { background:#4ea8ff20; color:var(--blue);  border:1px solid #4ea8ff40; }
  .badge.yellow { background:#f0b42920; color:var(--yellow);border:1px solid #f0b42940; }
  .chart-wrap { background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:20px; }
  .chart-stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:12px; margin-top:16px; }
  .stat .sl { font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:0.8px; margin-bottom:4px; }
  .stat .sv { font-size:16px; font-weight:700; font-family:'Syne',sans-serif; }
  .stat { background:#0f1219; border:1px solid var(--border); border-radius:6px; padding:10px 14px; }
  .no-data { text-align:center; padding:48px 20px; color:var(--muted); font-size:12px; }
  .pulse { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--green); animation:pulse 2s infinite; margin-right:5px; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.2} }
  footer { margin:24px 0 8px; text-align:center; font-size:10px; color:var(--muted); }
</style>
</head>
<body>
""")

        # Header
        html_parts.append(
            "<div class='header'>"
            "<h1>&#9889; Nifty Options</h1>"
            "<div class='header-meta'><span class='pulse'></span>PAPER TRADE &nbsp;&middot;&nbsp; ATM "
            + str(atm or "&mdash;") +
            " &nbsp;&middot;&nbsp; Last run: " + last_run +
            " &nbsp;&middot;&nbsp; Auto-refresh 5min</div></div>\n"
        )

        # Tab bar
        html_parts.append(
            "<div class='tab-bar'>"
            "<div class='tab active' onclick=\"switchTab('overview',this)\">&#128202; Overview</div>"
            "<div class='tab' onclick=\"switchTab('viewlog',this)\">&#129517; View Log "
            "<span class='tab-badge'>" + str(len(view_log)) + "</span></div>"
            "<div class='tab' onclick=\"switchTab('equity',this)\">&#128200; Equity Curve "
            "<span class='tab-badge'>" + str(len(equity_curve)) + "</span></div>"
            "</div>\n"
        )

        # ── Tab 1: Overview ────────────────────────────────────────────────
        html_parts.append("<div id='panel-overview' class='panel active'>\n<div class='grid'>\n")
        html_parts.append(
            "<div class='card'><div class='label'>ATM Strike</div>"
            "<div class='value'>" + str(atm or "&mdash;") + "</div>"
            "<div class='sub'>9:15 candle close</div></div>\n"
        )
        html_parts.append(
            "<div class='card'><div class='label'>Score / Label</div>"
            "<div class='value' style='font-size:14px;padding-top:4px'>"
            "<span style='background:" + view_col + "20;color:" + view_col + ";"
            "border:1px solid " + view_col + "60;padding:4px 14px;border-radius:20px;"
            "font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:1px'>"
            + view + "</span></div>"
            "<div class='sub'>Score: <b style='color:" + view_col + "'>" + score_disp + "</b>"
            " &nbsp;|&nbsp; Pending: " + str(pending) + "</div></div>\n"
        )
        html_parts.append(
            "<div class='card'><div class='label'>Entry View</div>"
            "<div class='value' style='font-size:15px'>" + str(entry_view).upper() + "</div>"
            "<div class='sub'>at 10:30 AM</div></div>\n"
        )
        html_parts.append(
            "<div class='card'><div class='label'>Total P&amp;L</div>"
            "<div class='value' style='color:" + pnl_col + "'>&#8377;" + f"{pnl:,.0f}" + "</div>"
            "<div class='sub'>" + str(len(positions)) + " legs</div></div>\n"
        )
        html_parts.append(
            "<div class='card'><div class='label'>Adjustments</div>"
            "<div class='value' style='color:" + adj_color + "'>" + str(adj_count) + "/4</div>"
            "<div class='sub'>Max 4 per day</div></div>\n"
        )
        html_parts.append(
            "<div class='card'><div class='label'>Trail SL</div>"
            "<div class='value' style='font-size:15px;color:" + tsl_color + "'>" + tsl_display + "</div>"
            "<div class='sub'>Peak: &#8377;" + f"{int(peak_pnl):,}" + "</div></div>\n"
        )
        html_parts.append(
            "<div class='card'><div class='label'>Status</div>"
            "<div class='value' style='font-size:12px;padding-top:6px'>" + sf + "</div>"
            "<div class='sub'>Paper Trade Mode</div></div>\n"
        )
        html_parts.append("</div>\n")  # end grid

        # Straddle table
        html_parts.append(
            "<div class='section'>"
            "<div class='section-hdr'><h2>&#128202; Straddle VWAP</h2>"
            "<span style='color:var(--muted);font-size:10px'>ATM &#177;400 &middot; 9 strikes &middot; 5-min</span></div>"
            "<table><thead><tr><th>Strike</th><th>Price</th><th>VWAP</th><th>vs VWAP</th></tr></thead>"
            "<tbody>" + straddle_html + "</tbody></table></div>\n"
        )

        # Positions table
        html_parts.append(
            "<div class='section'>"
            "<div class='section-hdr'><h2>&#128203; Positions</h2>"
            "<span style='color:var(--muted);font-size:10px'>Paper Trade</span></div>"
            "<table><thead><tr><th>St</th><th>Type</th><th>Strike</th><th>Action</th>"
            "<th>Lots</th><th>Delta</th><th>Time</th><th>Entry &#8377;</th><th>LTP &#8377;</th><th>P&amp;L</th></tr></thead>"
            "<tbody>" + pos_html + "</tbody></table></div>\n"
        )

        # Error table
        html_parts.append(
            "<div class='section'>"
            "<div class='section-hdr'><h2>&#128308; Error Log</h2>"
            "<span style='color:var(--muted);font-size:10px'>Last 10</span></div>"
            "<table><thead><tr><th>Time</th><th>Message</th></tr></thead>"
            "<tbody>" + err_html + "</tbody></table></div>\n"
        )
        html_parts.append("</div>\n")  # end panel-overview

        # ── Tab 2: View Log ────────────────────────────────────────────────
        html_parts.append(
            "<div id='panel-viewlog' class='panel'>\n"
            "<div class='section'>"
            "<div class='section-hdr'><h2>&#129517; Direction View Log</h2>"
            "<span style='color:var(--muted);font-size:10px'>"
            + str(len(view_log)) + " entries &middot; "
            + str(view_changes) + " changes &middot; newest first</span></div>"
            + vlog_table +
            "</div>\n</div>\n"
        )

        # ── Tab 3: Equity Curve ────────────────────────────────────────────
        html_parts.append(
            "<div id='panel-equity' class='panel'>\n"
            "<div id='eq-nodata' class='no-data' style='display:none'>"
            "No equity data yet &mdash; populates after 10:30 AM entry</div>\n"
            "<div id='eq-content' class='chart-wrap'>\n"
            "<canvas id='eqChart' style='max-height:400px'></canvas>\n"
            "<div class='chart-stats'>"
            "<div class='stat'><div class='sl'>Current P&amp;L</div><div class='sv' id='eq-last'>&mdash;</div></div>"
            "<div class='stat'><div class='sl'>Peak P&amp;L</div><div class='sv' id='eq-peak' style='color:var(--green)'>&mdash;</div></div>"
            "<div class='stat'><div class='sl'>Trough</div><div class='sv' id='eq-trough'>&mdash;</div></div>"
            "<div class='stat'><div class='sl'>Max Drawdown</div><div class='sv' id='eq-dd'>&mdash;</div></div>"
            "<div class='stat'><div class='sl'>Data Points</div><div class='sv' id='eq-pts' style='color:var(--muted)'>&mdash;</div></div>"
            "</div></div>\n</div>\n"
        )

        # Footer
        html_parts.append(
            "<footer><span class='pulse'></span>Nifty Options Paper Trader &middot; "
            + str(state.get("date","&mdash;")) + " &middot; &copy; "
            + str(datetime.now().year) + "</footer>\n"
        )

        # ── JS ─────────────────────────────────────────────────────────────
        html_parts.append("<script>\n")
        html_parts.append("const eqTimes = " + eq_times_json + ";\n")
        html_parts.append("const eqPnls  = " + eq_pnls_json  + ";\n")
        html_parts.append("""
let eqChartInited = false;

function switchTab(id, el) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + id).classList.add('active');
  el.classList.add('active');
  if (id === 'equity') initEquityChart();
}

function initEquityChart() {
  if (eqChartInited) return;
  eqChartInited = true;
  if (eqTimes.length === 0) {
    document.getElementById('eq-nodata').style.display = 'block';
    document.getElementById('eq-content').style.display = 'none';
    return;
  }
  const maxPnl  = Math.max(...eqPnls);
  const minPnl  = Math.min(...eqPnls);
  const lastPnl = eqPnls[eqPnls.length - 1];
  const fmt = v => '\u20b9' + v.toLocaleString('en-IN');
  document.getElementById('eq-last').textContent   = fmt(lastPnl);
  document.getElementById('eq-last').style.color   = lastPnl >= 0 ? '#00c896' : '#ff4e6a';
  document.getElementById('eq-peak').textContent   = fmt(maxPnl);
  document.getElementById('eq-trough').textContent = fmt(minPnl);
  document.getElementById('eq-trough').style.color = minPnl < 0 ? '#ff4e6a' : '#00c896';
  document.getElementById('eq-pts').textContent    = eqTimes.length + ' pts';
  const dd = eqPnls.reduce((acc, v, i) => {
    const pk = Math.max(...eqPnls.slice(0, i + 1));
    return Math.min(acc, v - pk);
  }, 0);
  document.getElementById('eq-dd').textContent   = fmt(dd);
  document.getElementById('eq-dd').style.color   = dd < 0 ? '#ff4e6a' : '#00c896';
  const lineColor = lastPnl >= 0 ? '#00c896' : '#ff4e6a';
  const ctx  = document.getElementById('eqChart').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 400);
  grad.addColorStop(0, lastPnl >= 0 ? '#00c89650' : '#ff4e6a50');
  grad.addColorStop(1, 'rgba(0,0,0,0)');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: eqTimes,
      datasets: [{
        label: 'P&L',
        data: eqPnls,
        borderColor: lineColor,
        borderWidth: 2,
        backgroundColor: grad,
        fill: true,
        tension: 0.3,
        pointRadius: eqTimes.length > 40 ? 0 : 3,
        pointHoverRadius: 5,
        pointBackgroundColor: lineColor,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#12161f', borderColor: '#1e2433', borderWidth: 1,
          titleColor: '#c9d1e0', bodyColor: '#00c896',
          callbacks: { label: c => ' \u20b9' + c.raw.toLocaleString('en-IN') }
        }
      },
      scales: {
        x: { ticks: { color:'#5a6480', font:{size:10}, maxTicksLimit:12 }, grid:{color:'#1e243360'} },
        y: { ticks: { color:'#5a6480', font:{size:10}, callback: v => '\u20b9'+v.toLocaleString('en-IN') },
             grid: { color:'#1e243360' } }
      }
    }
  });
}
</script>
</body>
</html>""")

        final_html = "".join(html_parts)

        with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
            f.write(final_html)
        log.info(f"Dashboard written -> {DASHBOARD_FILE} ({len(final_html)} bytes)")

    except Exception as e:
        log.error(f"generate_dashboard error: {e}", exc_info=True)
