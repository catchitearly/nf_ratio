"""
dashboard_gen.py
Generates a 3-tab static HTML dashboard:
  Tab 1 - Overview   : KPIs, Straddle VWAP, Positions, Errors
  Tab 2 - View Log   : Timestamped direction view history
  Tab 3 - Equity Curve: Interactive P&L chart over the day
"""

import json
import logging
import os
from datetime import datetime
from config import DASHBOARD_FILE
from position_manager import total_pnl

log = logging.getLogger(__name__)

VIEW_COLOR = {"bullish": "#00c896", "bearish": "#ff4e6a", "neutral": "#f0b429", None: "#888"}

def _pnl_color(val: float) -> str:
    return "#00c896" if val >= 0 else "#ff4e6a"

def _fmt_time(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except Exception:
        return iso or "—"

def _view_badge(view: str) -> str:
    col = VIEW_COLOR.get(view, "#888")
    return f"<span style='background:{col}20;color:{col};border:1px solid {col}60;padding:2px 10px;border-radius:12px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1px'>{view or '—'}</span>"

def _straddle_rows(snapshot: dict, atm: int) -> str:
    if not snapshot:
        return "<tr><td colspan='4' style='text-align:center;color:var(--muted)'>No straddle data yet</td></tr>"
    rows = ""
    for strike in sorted(snapshot.keys(), key=lambda x: int(x), reverse=True):
        s     = snapshot[strike]
        is_atm = int(strike) == atm
        label  = f"<b style='color:#fff'>{strike} ★</b>" if is_atm else strike
        price  = s.get("current_price")
        vwap   = s.get("vwap")
        below  = s.get("below_vwap", False)
        above  = s.get("above_vwap", False)
        rel    = "↓ Below" if below else "↑ Above" if above else "—"
        rc     = "#00c896" if below else "#ff4e6a" if above else "#5a6480"
        bg     = "background:#ffffff08;" if is_atm else ""
        rows  += (
            f"<tr style='{bg}'>"
            f"<td>{label}</td>"
            f"<td>{f'{price:.1f}' if price else '—'}</td>"
            f"<td>{f'{vwap:.1f}' if vwap else '—'}</td>"
            f"<td style='color:{rc};font-weight:700'>{rel}</td>"
            f"</tr>"
        )
    return rows

def _position_rows(positions: list[dict]) -> str:
    if not positions:
        return "<tr><td colspan='8' style='text-align:center;color:var(--muted)'>No positions yet</td></tr>"
    rows = ""
    for p in positions:
        pnl    = p.get("pnl", 0)
        pc     = _pnl_color(pnl)
        closed = "<span style='color:#00c896'>✓</span>" if p.get("closed") else "<span style='color:#f0b429'>●</span>"
        act_c  = "#ff4e6a" if p.get("action") == "SELL" else "#4ea8ff"
        rows  += (
            f"<tr>"
            f"<td>{closed}</td>"
            f"<td style='color:{'#38bdf8' if p.get('opt_type')=='CE' else '#f97316'};font-weight:700'>{p.get('opt_type','')}</td>"
            f"<td>{p.get('strike','')}</td>"
            f"<td style='color:{act_c};font-weight:700'>{p.get('action','')}</td>"
            f"<td>{p.get('lots','')}</td>"
            f"<td>₹{p.get('entry_price',0):.1f}</td>"
            f"<td>₹{p.get('current_ltp',0):.1f}</td>"
            f"<td style='color:{pc};font-weight:700'>₹{pnl:,.0f}</td>"
            f"</tr>"
        )
    return rows

def _error_rows(errors: list[dict]) -> str:
    if not errors:
        return "<tr><td colspan='2' style='text-align:center;color:var(--muted)'>No errors</td></tr>"
    rows = ""
    for e in reversed(errors[-10:]):
        rows += (
            f"<tr><td style='white-space:nowrap;color:var(--muted)'>{_fmt_time(e.get('time'))}</td>"
            f"<td style='color:#ff4e6a'>{e.get('msg','')[:150]}</td></tr>"
        )
    return rows

def _view_log_rows(view_log: list[dict]) -> str:
    if not view_log:
        return "<tr><td colspan='5' style='text-align:center;color:var(--muted)'>No view data yet</td></tr>"
    rows = ""
    prev_view = None
    for entry in reversed(view_log):
        v        = entry.get("view", "")
        changed  = v != prev_view and prev_view is not None
        row_bg   = "background:#ffffff06;" if changed else ""
        change_mark = "<span style='color:#f0b429;margin-left:6px'>⟳ CHANGED</span>" if changed else ""
        confirmed = entry.get("confirmed", "")
        pending   = entry.get("pending", "")
        confirmed_html = _view_badge(confirmed) if confirmed else "<span style='color:var(--muted)'>&mdash;</span>"
        pending_html   = _view_badge(pending)   if pending   else "&mdash;"
        spot_val       = entry.get('spot', '&mdash;')
        time_val       = entry.get('time', '&mdash;')
        rows += (
            "<tr style='" + row_bg + "'>"
            "<td style='color:var(--muted);white-space:nowrap'>" + time_val + "</td>"
            "<td>" + _view_badge(v) + change_mark + "</td>"
            "<td>" + confirmed_html + "</td>"
            "<td style='color:var(--muted)'>" + pending_html + "</td>"
            "<td style='font-family:monospace'>&#8377;" + str(spot_val) + "</td>"
            "</tr>"
        )
        prev_view = v
    return rows


def generate_dashboard(state: dict):
    """Write full 3-tab HTML dashboard to DASHBOARD_FILE."""
    try:
        os.makedirs(os.path.dirname(DASHBOARD_FILE), exist_ok=True)

        atm          = state.get("atm") or 0
        view         = state.get("current_view") or "—"
        pending      = state.get("pending_view") or "—"
        entry_view   = state.get("entry_view") or "—"
        last_run     = _fmt_time(state.get("last_run"))
        positions    = state.get("positions", [])
        errors       = state.get("errors", [])
        entry_done   = state.get("entry_done", False)
        closed       = state.get("closed", False)
        add_done     = state.get("add_1135_done", False)
        snapshot     = state.get("straddle_snapshot", {})
        view_log     = state.get("view_log", [])
        equity_curve = state.get("equity_curve", [])
        pnl          = total_pnl(positions)
        view_col     = VIEW_COLOR.get(view if view != "—" else None, "#888")
        pnl_col      = _pnl_color(pnl)

        straddle_rows_html = _straddle_rows(snapshot, atm)
        pos_rows_html      = _position_rows(positions)
        err_rows_html      = _error_rows(errors)
        view_log_rows_html = _view_log_rows(view_log)

        status_flags = ""
        if entry_done:  status_flags += "<span class='badge green'>ENTRY DONE</span> "
        if add_done:    status_flags += "<span class='badge blue'>1135 ADD DONE</span> "
        if closed:      status_flags += "<span class='badge red'>CLOSED</span> "
        if not status_flags: status_flags = "<span class='badge yellow'>WAITING</span>"

        # Equity curve data for chart
        eq_times = json.dumps([e["time"] for e in equity_curve])
        eq_pnls  = json.dumps([e["pnl"]  for e in equity_curve])

        # View log count for badge
        view_changes = sum(
            1 for i in range(1, len(view_log))
            if view_log[i]["view"] != view_log[i-1]["view"]
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta http-equiv="refresh" content="300"/>
<title>Nifty Options | Paper Trade</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@700;800&display=swap');
  :root {{
    --bg:      #0b0e14; --surface: #12161f; --border: #1e2433;
    --text:    #c9d1e0; --muted:   #5a6480;
    --green:   #00c896; --red:     #ff4e6a;
    --yellow:  #f0b429; --blue:    #4ea8ff; --accent: #7c5cff;
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); font-family:'JetBrains Mono',monospace; font-size:13px; min-height:100vh; }}
  /* ── Header ── */
  .header {{ display:flex; align-items:center; justify-content:space-between; padding:16px 24px;
             border-bottom:1px solid var(--border); background:var(--surface); flex-wrap:wrap; gap:8px; }}
  .header h1 {{ font-family:'Syne',sans-serif; font-size:22px; font-weight:800; color:#fff; letter-spacing:-0.5px; }}
  .header-meta {{ font-size:11px; color:var(--muted); }}
  /* ── Tabs ── */
  .tab-bar {{ display:flex; gap:0; border-bottom:1px solid var(--border); background:var(--surface); padding:0 24px; }}
  .tab {{ padding:12px 20px; font-size:12px; font-weight:700; text-transform:uppercase; letter-spacing:0.8px;
          color:var(--muted); cursor:pointer; border-bottom:2px solid transparent; transition:all 0.15s; white-space:nowrap; }}
  .tab:hover {{ color:var(--text); }}
  .tab.active {{ color:#fff; border-bottom-color:var(--accent); }}
  .tab-badge {{ display:inline-block; background:var(--accent); color:#fff; border-radius:8px;
                font-size:9px; padding:1px 5px; margin-left:6px; vertical-align:middle; }}
  /* ── Tab panels ── */
  .panel {{ display:none; padding:20px 24px; }}
  .panel.active {{ display:block; }}
  /* ── KPI grid ── */
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:20px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:14px 16px; }}
  .card .label {{ font-size:10px; text-transform:uppercase; letter-spacing:1px; color:var(--muted); margin-bottom:6px; }}
  .card .value {{ font-family:'Syne',sans-serif; font-size:20px; font-weight:700; color:#fff; }}
  .card .sub {{ font-size:10px; color:var(--muted); margin-top:4px; }}
  /* ── Sections ── */
  .section {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; margin-bottom:16px; overflow:hidden; }}
  .section-hdr {{ display:flex; align-items:center; justify-content:space-between;
                  padding:10px 16px; border-bottom:1px solid var(--border); background:#0f1219; }}
  .section-hdr h2 {{ font-family:'Syne',sans-serif; font-size:12px; font-weight:700;
                     text-transform:uppercase; letter-spacing:1px; color:#fff; }}
  /* ── Tables ── */
  table {{ width:100%; border-collapse:collapse; }}
  th {{ padding:8px 12px; text-align:left; font-size:10px; text-transform:uppercase;
        letter-spacing:0.8px; color:var(--muted); border-bottom:1px solid var(--border); background:#0f1219; }}
  td {{ padding:8px 12px; border-bottom:1px solid #161b26; vertical-align:middle; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:#161b2688; }}
  /* ── Badges ── */
  .badge {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; margin-right:4px; }}
  .badge.green  {{ background:#00c89620; color:var(--green); border:1px solid #00c89640; }}
  .badge.red    {{ background:#ff4e6a20; color:var(--red);   border:1px solid #ff4e6a40; }}
  .badge.blue   {{ background:#4ea8ff20; color:var(--blue);  border:1px solid #4ea8ff40; }}
  .badge.yellow {{ background:#f0b42920; color:var(--yellow);border:1px solid #f0b42940; }}
  /* ── View pill ── */
  .view-pill {{ display:inline-block; padding:4px 14px; border-radius:20px; font-weight:700;
                font-size:11px; text-transform:uppercase; letter-spacing:1px;
                background:{view_col}20; color:{view_col}; border:1px solid {view_col}60; }}
  /* ── Equity chart ── */
  .chart-wrap {{ background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:20px; }}
  .chart-wrap canvas {{ max-height:380px; }}
  .chart-stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:12px; margin-top:16px; }}
  .stat {{ background:#0f1219; border:1px solid var(--border); border-radius:6px; padding:10px 14px; }}
  .stat .sl {{ font-size:10px; color:var(--muted); text-transform:uppercase; letter-spacing:0.8px; margin-bottom:4px; }}
  .stat .sv {{ font-size:16px; font-weight:700; font-family:'Syne',sans-serif; }}
  /* ── Pulse ── */
  .pulse {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--green); animation:pulse 2s infinite; margin-right:5px; }}
  @keyframes pulse {{ 0%,100% {{opacity:1}} 50% {{opacity:0.2}} }}
  /* ── No-data ── */
  .no-data {{ text-align:center; padding:48px 20px; color:var(--muted); font-size:12px; }}
  footer {{ margin:24px 0 8px; text-align:center; font-size:10px; color:var(--muted); padding:0 24px; }}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <h1>⚡ Nifty Options</h1>
  <div class="header-meta">
    <span class="pulse"></span>PAPER TRADE &nbsp;·&nbsp; ATM {atm or '—'} &nbsp;·&nbsp; Last run: {last_run} &nbsp;·&nbsp; Auto-refresh 5min
  </div>
</div>

<!-- Tab bar -->
<div class="tab-bar">
  <div class="tab active" onclick="switchTab('overview',this)">📊 Overview</div>
  <div class="tab" onclick="switchTab('viewlog',this)">🧭 View Log <span class="tab-badge">{len(view_log)}</span></div>
  <div class="tab" onclick="switchTab('equity',this)">📈 Equity Curve <span class="tab-badge">{len(equity_curve)}</span></div>
</div>

<!-- ═══════════════════════════════ TAB 1: OVERVIEW ═══════════════════════════════ -->
<div id="panel-overview" class="panel active">

  <!-- KPI Cards -->
  <div class="grid">
    <div class="card">
      <div class="label">ATM Strike</div>
      <div class="value">{atm or '—'}</div>
      <div class="sub">9:15 candle close</div>
    </div>
    <div class="card">
      <div class="label">Current View</div>
      <div class="value" style="font-size:14px;padding-top:4px"><span class="view-pill">{view}</span></div>
      <div class="sub">Pending: {pending}</div>
    </div>
    <div class="card">
      <div class="label">Entry View</div>
      <div class="value" style="font-size:15px">{entry_view.upper()}</div>
      <div class="sub">at 10:30 AM</div>
    </div>
    <div class="card">
      <div class="label">Total P&amp;L</div>
      <div class="value" style="color:{pnl_col}">₹{pnl:,.0f}</div>
      <div class="sub">{len(positions)} legs</div>
    </div>
    <div class="card">
      <div class="label">Status</div>
      <div class="value" style="font-size:12px;padding-top:6px">{status_flags}</div>
      <div class="sub">Paper Trade Mode</div>
    </div>
  </div>

  <!-- Straddle VWAP -->
  <div class="section">
    <div class="section-hdr">
      <h2>📊 Straddle VWAP Status</h2>
      <span style="color:var(--muted);font-size:10px">ATM ±400 · 9 strikes · 5-min candles</span>
    </div>
    <table>
      <thead><tr><th>Strike</th><th>Straddle Price</th><th>VWAP</th><th>vs VWAP</th></tr></thead>
      <tbody>{straddle_rows_html}</tbody>
    </table>
  </div>

  <!-- Positions -->
  <div class="section">
    <div class="section-hdr">
      <h2>📋 Positions</h2>
      <span style="color:var(--muted);font-size:10px">Paper Trade · All legs</span>
    </div>
    <table>
      <thead>
        <tr><th>St</th><th>Type</th><th>Strike</th><th>Action</th><th>Lots</th><th>Entry ₹</th><th>LTP ₹</th><th>P&amp;L</th></tr>
      </thead>
      <tbody>{pos_rows_html}</tbody>
    </table>
  </div>

  <!-- Errors -->
  <div class="section">
    <div class="section-hdr">
      <h2>🔴 Error Log</h2>
      <span style="color:var(--muted);font-size:10px">Last 10</span>
    </div>
    <table>
      <thead><tr><th>Time</th><th>Message</th></tr></thead>
      <tbody>{err_rows_html}</tbody>
    </table>
  </div>
</div>

<!-- ═══════════════════════════════ TAB 2: VIEW LOG ═══════════════════════════════ -->
<div id="panel-viewlog" class="panel">
  <div class="section">
    <div class="section-hdr">
      <h2>🧭 Direction View Log</h2>
      <span style="color:var(--muted);font-size:10px">{len(view_log)} entries · {view_changes} view changes · newest first</span>
    </div>
    {'<table><thead><tr><th>Time</th><th>Raw View</th><th>Confirmed</th><th>Pending</th><th>Spot</th></tr></thead><tbody>' + view_log_rows_html + '</tbody></table>'
     if view_log else '<div class="no-data">No view data yet — runs after 9:15 AM</div>'}
  </div>
</div>

<!-- ═══════════════════════════════ TAB 3: EQUITY CURVE ═══════════════════════════════ -->
<div id="panel-equity" class="panel">
  {'_EQUITY_CONTENT_' if equity_curve else '<div class="no-data">No equity data yet — populates after 10:30 AM entry</div>'}
</div>

<footer>
  <span class="pulse"></span>
  Nifty Options Paper Trader &nbsp;·&nbsp; Expiry {state.get('date','—')} &nbsp;·&nbsp;
  GitHub Actions + cron-job.org &nbsp;·&nbsp; © {datetime.now().year}
</footer>

<script>
// ── Tab switching ──────────────────────────────────────────────────────────
function switchTab(id, el) {{
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + id).classList.add('active');
  el.classList.add('active');
}}

// ── Equity curve chart ─────────────────────────────────────────────────────
const eqTimes = {eq_times};
const eqPnls  = {eq_pnls};

if (eqTimes.length > 0) {{
  const maxPnl  = Math.max(...eqPnls);
  const minPnl  = Math.min(...eqPnls);
  const lastPnl = eqPnls[eqPnls.length - 1];

  // Stats
  document.getElementById('eq-last').textContent  = '₹' + lastPnl.toLocaleString('en-IN');
  document.getElementById('eq-last').style.color  = lastPnl >= 0 ? '#00c896' : '#ff4e6a';
  document.getElementById('eq-peak').textContent  = '₹' + maxPnl.toLocaleString('en-IN');
  document.getElementById('eq-trough').textContent = '₹' + minPnl.toLocaleString('en-IN');
  document.getElementById('eq-trough').style.color = minPnl < 0 ? '#ff4e6a' : '#00c896';
  const dd = eqPnls.reduce((acc, v, i) => {{
    const pk = Math.max(...eqPnls.slice(0, i + 1));
    return Math.min(acc, v - pk);
  }}, 0);
  document.getElementById('eq-dd').textContent = '₹' + dd.toLocaleString('en-IN');
  document.getElementById('eq-dd').style.color = dd < 0 ? '#ff4e6a' : '#00c896';
  document.getElementById('eq-pts').textContent = eqTimes.length + ' pts';

  // Gradient fill
  const ctx  = document.getElementById('eqChart').getContext('2d');
  const grad = ctx.createLinearGradient(0, 0, 0, 380);
  grad.addColorStop(0, lastPnl >= 0 ? '#00c89640' : '#ff4e6a40');
  grad.addColorStop(1, '#00000000');

  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: eqTimes,
      datasets: [{{
        label: 'P&L (₹)',
        data: eqPnls,
        borderColor: lastPnl >= 0 ? '#00c896' : '#ff4e6a',
        borderWidth: 2,
        backgroundColor: grad,
        fill: true,
        tension: 0.3,
        pointRadius: eqTimes.length > 40 ? 0 : 3,
        pointHoverRadius: 5,
        pointBackgroundColor: lastPnl >= 0 ? '#00c896' : '#ff4e6a',
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: true,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '#12161f',
          borderColor: '#1e2433',
          borderWidth: 1,
          titleColor: '#c9d1e0',
          bodyColor: '#00c896',
          callbacks: {{
            label: ctx => ' ₹' + ctx.raw.toLocaleString('en-IN')
          }}
        }}
      }},
      scales: {{
        x: {{
          ticks: {{ color: '#5a6480', font: {{ size: 10 }}, maxTicksLimit: 12 }},
          grid:  {{ color: '#1e243360' }}
        }},
        y: {{
          ticks: {{ color: '#5a6480', font: {{ size: 10 }},
                    callback: v => '₹' + v.toLocaleString('en-IN') }},
          grid: {{ color: '#1e243360' }},
          border: {{ dash: [4,4] }}
        }}
      }}
    }}
  }});
}}
</script>

</body>
</html>"""

        # Inject equity panel content (needs chart canvas + stat cards)
        equity_content = f"""
  <div class="chart-wrap">
    <canvas id="eqChart"></canvas>
    <div class="chart-stats">
      <div class="stat"><div class="sl">Current P&L</div><div class="sv" id="eq-last">—</div></div>
      <div class="stat"><div class="sl">Peak P&L</div><div class="sv" id="eq-peak" style="color:var(--green)">—</div></div>
      <div class="stat"><div class="sl">Trough P&L</div><div class="sv" id="eq-trough">—</div></div>
      <div class="stat"><div class="sl">Max Drawdown</div><div class="sv" id="eq-dd">—</div></div>
      <div class="stat"><div class="sl">Data Points</div><div class="sv" id="eq-pts" style="color:var(--muted)">—</div></div>
    </div>
  </div>"""

        html = html.replace("'_EQUITY_CONTENT_'", equity_content)

        with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
            f.write(html)
        log.info(f"Dashboard written → {DASHBOARD_FILE}")

    except Exception as e:
        log.error(f"generate_dashboard error: {e}")
