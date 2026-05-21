# Nifty Options Paper Trader

Automated Nifty options strategy with dynamic directional view, paper trading, GitHub Actions execution, and a live GitHub Pages dashboard.

---

## File Structure

```
├── main.py              # Entry point
├── strategy.py          # Main orchestrator (called every 5 min)
├── config.py            # All constants and settings
├── fyers_data.py        # Fyers REST API: OHLC + quotes + greeks
├── vwap.py              # Straddle VWAP computation
├── view_engine.py       # Bull / Bear / Neutral view logic
├── position_manager.py  # Paper trade leg builder + MTM + delta
├── telegram_bot.py      # Telegram alerts
├── dashboard_gen.py     # Static HTML dashboard generator
├── state_manager.py     # Persistent state.json read/write
├── state.json           # Auto-generated runtime state
├── docs/index.html      # Dashboard (served via GitHub Pages)
├── logs/                # Daily log files
├── requirements.txt
└── .github/workflows/strategy.yml
```

---

## Setup

### 1. GitHub Secrets
Add these in `Settings → Secrets → Actions`:

| Secret | Value |
|---|---|
| `FYERS_CLIENT_ID` | Your Fyers Client ID |
| `FYERS_ACCESS_TOKEN` | Your Fyers daily access token |
| `TELEGRAM_BOT_TOKEN` | Your Telegram bot token |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |

> ⚠️ Fyers access token expires daily. You need to refresh it each morning and update the secret, or automate token refresh separately.

### 2. GitHub Pages
- Go to `Settings → Pages`
- Source: `Deploy from branch`
- Branch: `main`, folder: `/docs`
- Dashboard will be live at `https://<username>.github.io/<repo>/`

### 3. cron-job.org (true 5-min trigger)
GitHub Actions native cron has ~1-5 min delay and minimum 5-min interval. For reliable 5-min execution:
1. Go to [cron-job.org](https://cron-job.org)
2. Create a new job
3. URL: `https://api.github.com/repos/<USERNAME>/<REPO>/actions/workflows/strategy.yml/dispatches`
4. Method: POST
5. Headers: `Authorization: Bearer <GITHUB_PAT>`, `Accept: application/vnd.github+json`
6. Body: `{"ref":"main"}`
7. Schedule: Every 5 minutes
8. This triggers `workflow_dispatch` precisely every 5 minutes

---

## Strategy Logic

### ATM
- 9:15 candle close → rounded to nearest 100 → fixed for the day

### View (evaluated every 5 min)
| View | Condition |
|---|---|
| **Bullish** | ATM below VWAP **AND** ≥3 of ATM+100→+400 below VWAP **AND** ≥2 of ATM-400→-100 above VWAP |
| **Bearish** | ≥3 of ATM+100→+400 above VWAP **AND** ≥2 of ATM-400→-100 below VWAP |
| **Neutral** | Neither condition met |

View change requires **2 consecutive 5-min bars** to be confirmed.

### Entry (10:30 AM)
| View | CE Sell | PE Sell |
|---|---|---|
| Neutral | 3 lots | 3 lots |
| Bullish | 2 lots | 4 lots |
| Bearish | 4 lots | 2 lots |

- Sell strike: delta 0.10–0.15
- Hedge: 100 points farther OTM (same qty as sell)

### Adjustments
- **View change confirmed**: Add 3 lots on newly favored side + hedge
- **11:35 AM** (view unchanged since entry): One-time add at same entry ratio
- **Delta ≥ 0.35**: Telegram alert (no auto-close)

### Exit
- All positions closed at **3:00 PM** flat

---

## Paper Trade Mode
`PAPER_TRADE = True` in config.py — no real orders are placed. All trades are simulated and logged to state.json and the dashboard.

---

## Updating Fyers Token Daily
The Fyers access token must be refreshed each trading day. Options:
1. Manual: Regenerate token each morning, update GitHub Secret via API or UI
2. Automated: Add a separate token-refresh workflow (Fyers OAuth flow)
