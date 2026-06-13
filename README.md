# 📈 Squeeze Bot

A lightweight Discord bot for short squeeze setup monitoring and alerting.

Squeeze Bot scans a stock universe during market hours, scores potential short-squeeze candidates, caches the latest results, tracks candidate state over the trading day, and posts clean Discord embed alerts only when meaningful setup changes occur.

---

## ⚡ Features

- Scheduled market-hours scans
- Manual admin scan trigger
- Cached top squeeze candidates
- Live single-ticker reports
- Short-interest recovery for missing candidate data
- Alert cooldowns and score-improvement re-alerts
- Daily candidate state tracking
- Event-style alert intelligence
- Upgrade, realert, cooling-off, reactivation, and quiet expiration detection
- Reason-builder system with drivers and risks
- Display-only Structure / Activation / Health / Risk breakdown
- Public read-only slash commands
- Consolidated admin command
- Persistent cache and logs
- Docker Compose deployment

---

## 🧠 Phase 6 Alert Intelligence

Phase 6 moves the bot from a repeated scanner into a setup monitor.

Instead of only asking “did this stock move?”, the bot now tracks:

```text
squeeze-prone structure + live activation + strengthening/fading state over time
```

The alert-state layer records daily-ish ticker state such as:

- first seen
- last seen
- last alert
- current score
- previous score
- score change
- peak score today
- current classification
- previous classification
- peak classification today
- current event type
- last event type
- active / cooling-off / expired status
- alert count today

Supported event-style states include:

```text
NEW_DISCOVERY
UPGRADE
DOWNGRADE
SCORE_SURGE
NEW_PEAK_SCORE
STILL_ACTIVE
COOLING_OFF
EXPIRED
REACTIVATED
```

Expiration is intentionally quiet for now. Expired tickers are saved for inspection and recap, but expiration alerts are not posted automatically.

---

## 🤖 Discord Commands

### Public

| Command | Purpose |
| --- | --- |
| `/squeeze status` | Show bot and market status |
| `/squeeze top` | Show latest cached top candidates |
| `/squeeze ticker symbol:...` | Build a live one-ticker report |
| `/squeeze alerts` | Show recent alert-state summary |
| `/squeeze alerts symbol:...` | Show alert state for one ticker |
| `/squeeze explain symbol:...` | Explain one ticker using drivers, risks, and score components |
| `/squeeze recap` | Show a daily setup-monitor recap |

### Admin

All admin controls live under:

```text
/squeeze admin
```

Supported actions:

| Action | Purpose |
| --- | --- |
| `scan` | Run scanner manually |
| `refresh-shorts` | Refresh missing short-interest data |
| `refresh-shorts symbol:...` | Refresh short interest for one ticker |
| `universe` | Show universe summary |
| `universe refresh:true` | Refresh universe data |
| `alert-state symbol:...` | Inspect alert state for one ticker |
| `clear-alerts` | Clear all alert state |
| `clear-alerts symbol:...` | Clear alert state for one ticker |

---

## 🧠 How It Works

1. Loads the ticker universe
2. Pulls price and volume data through yfinance
3. Applies configured squeeze thresholds
4. Scores and ranks candidates
5. Caches the latest result set
6. Updates candidate state for every seen candidate
7. Refreshes missing short-interest data when needed
8. Detects meaningful setup events
9. Posts eligible alerts to Discord
10. Uses cooldown rules to avoid spam
11. Saves quiet state changes for inspection and recap

---

## ⚙️ Configuration

Copy the example environment file:

```bash
cp .env.example .env
```

Fill in your Discord credentials:

```env
DISCORD_TOKEN=
DISCORD_CHANNEL_ID=
DISCORD_GUILD_ID=
DISCORD_ADMIN_IDS=
```

Recommended production settings:

```env
MARKET_TIMEZONE=America/New_York
MARKET_OPEN=09:30
MARKET_CLOSE=16:00
SCAN_MARKET_HOURS_ONLY=true

MIN_PRICE=3.00
MIN_AVG_VOLUME=500000
MAX_TICKERS=2500

SQUEEZE_SHORT_THRESH=0.20
SQUEEZE_REL_VOL_THRESH=1.35
SQUEEZE_PCT_MOVE_THRESH=2.00

SCAN_INTERVAL_MINUTES=15
MIN_SECONDS_BETWEEN_SCANS=600

MAX_ALERTS_PER_SCAN=5
SCHEDULED_ALERTS_PER_SCAN=2
ALERT_POST_SPACING_SECONDS=90

DISCOVERY_MAX_RANK=10
HEATING_UP_MIN_DISCOVERY_SCORE=55

ENABLE_SCORE_IMPROVEMENT_REALERT=true
REALERT_SCORE_IMPROVEMENT=10
REALERT_RANK_IMPROVEMENT=3

ALERT_COOLDOWN_MODE=trading_day
ALERT_COOLDOWN_MINUTES=60
EXPIRATION_MISSED_SCANS=2

FORCE_MARKET_OPEN_FOR_TESTING=false
```

> Keep `FORCE_MARKET_OPEN_FOR_TESTING=false` in production.

### Cooldown modes

`ALERT_COOLDOWN_MODE=trading_day` keeps the bot conservative and day-aware.

`ALERT_COOLDOWN_MODE=minutes` enables minute-based cooldown checks using `ALERT_COOLDOWN_MINUTES`. This is useful after Phase 6 has been watched during real market hours.

---

## ▶️ Local Development

### Install

```bash
python3 -m venv venv
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
```

Edit `.env`, then run:

```bash
python3 bot.py
```

### Verify

```bash
python3 -m py_compile bot.py config.py src/alert_state.py src/discord_commands.py src/discord_embeds.py src/reason_builder.py src/squeeze_scanner.py
```

---

## 🧪 Phase 6 Test Checklist

After applying Phase 6, test these commands in Discord:

```text
/squeeze status
/squeeze admin action:scan
/squeeze top
/squeeze alerts
/squeeze alerts symbol:GME
/squeeze admin action:alert-state symbol:GME
/squeeze explain symbol:GME
/squeeze recap
```

What to verify:

- Bot starts cleanly
- Slash commands respond
- Manual scan works
- Scheduled scan loop still starts
- Candidate state is saved
- Alerts are not posted for every repeated candidate
- Drivers and risks display cleanly
- Recap returns useful daily state
- Quiet expiration does not post automatic expiration alerts

---

## 🌿 Branch / Release Workflow

Before applying a major patch:

```bash
git status
git checkout -b phase6-before-finish
git add .
git commit -m "Checkpoint before finishing Phase 6"
```

Apply the Phase 6 files on a separate branch:

```bash
git checkout -b phase6-finish
git add .
git commit -m "Finish Phase 6 setup monitoring"
```

Tag a stable version after testing:

```bash
git tag v0.6.0-phase6
git push origin phase6-finish
git push origin v0.6.0-phase6
```

If rollback is needed:

```bash
git checkout phase6-before-finish
```

---

## 🐳 Docker

### Build

```bash
docker compose build
```

### Run

```bash
docker compose up
```

### Run in background

```bash
docker compose up -d
```

### View logs

```bash
docker compose logs -f
```

### Stop

```bash
docker compose down
```

---

## 📁 Project Structure

```text
Squeeze-Bot/

├── bot.py                    # Discord bot entrypoint
├── config.py                 # Environment/config loader
├── requirements.txt          # Python dependencies
├── Dockerfile                # Docker image definition
├── docker-compose.yml        # Docker Compose service config
├── .env.example              # Safe environment template
│
├── src/
│   ├── alert_state.py        # Daily candidate state and event tracking
│   ├── discord_commands.py   # Slash commands and admin controls
│   ├── discord_embeds.py     # Discord embed builders
│   ├── reason_builder.py     # Drivers, risks, and display component summaries
│   ├── squeeze_scanner.py    # Scanner, scoring, caching, ticker reports
│   ├── market_hours.py       # Market-hours logic
│   ├── universe_manager.py   # Ticker universe helpers
│   └── ...
│
├── cache/                    # Runtime cache files
└── logs/                     # Runtime logs
```

---

## ✅ Current Status

Completed:

- Local bot flow
- Market-hours guard
- Universe scanning
- yfinance chunking and retry safety
- Scanner lock and cooldown
- Candidate cache
- Short-interest recovery
- Single-ticker reports
- Alert state logic
- Daily candidate monitoring
- Event-style scheduled alerts
- Upgrade, reactivation, score-surge, peak-score, and cooling-off detection
- Quiet expiration tracking
- Drivers / risks reason builder
- Display-only score component breakdown
- `/squeeze explain`
- `/squeeze recap`
- Scheduled scan loop
- Public slash commands
- Consolidated admin command
- Docker deployment files

Not yet in scope:

- Heavy external data feeds
- Full score architecture rewrite
- Automatic expiration alert posting
- Trading recommendations or trade execution

---

## ⚠️ Notes

- SPY, QQQ, and IWM are better treated as market context symbols than normal squeeze candidates.
- ETF short interest should not be expected to reliably come from yfinance.
- Phase 6 component scores are display/explanation helpers and do not change scanner ranking unless intentionally wired into scanner scoring later.

---

## Disclaimer

This bot is for research, alerts, and personal market monitoring only.

It does not provide financial advice. Validate all signals before trading.

---
