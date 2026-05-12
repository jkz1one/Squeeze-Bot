# 📈 Squeeze Bot

A lightweight Discord bot for short squeeze alerts.

Squeeze Bot scans a stock universe during market hours, scores potential squeeze candidates, caches the latest results, and posts clean Discord embed alerts with cooldown protection.

---

## ⚡ Features

- Scheduled market-hours scans
- Manual admin scan trigger
- Cached top squeeze candidates
- Live single-ticker reports
- Short-interest recovery for missing candidate data
- Alert cooldowns and score-improvement re-alerts
- Public read-only slash commands
- Consolidated admin command
- Persistent cache and logs
- Docker Compose deployment

---

## 🤖 Discord Commands

### Public

| Command | Purpose |
| --- | --- |
| `/squeeze status` | Show bot and market status |
| `/squeeze top` | Show latest cached top candidates |
| `/squeeze ticker symbol:...` | Build a live one-ticker report |
| `/squeeze alerts` | Show alert-state summary |
| `/squeeze alerts symbol:...` | Show alert state for one ticker |

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
6. Refreshes missing short-interest data when needed
7. Posts eligible alerts to Discord
8. Uses cooldown rules to avoid spam

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

Important production settings:

```env
MARKET_TIMEZONE=America/New_York
MARKET_OPEN=09:30
MARKET_CLOSE=16:00
SCAN_MARKET_HOURS_ONLY=true

MIN_PRICE=3.00
MIN_AVG_VOLUME=300000
MAX_TICKERS=0

SCAN_INTERVAL_MINUTES=15
MIN_SECONDS_BETWEEN_SCANS=600

MAX_ALERTS_PER_SCAN=5
SCHEDULED_ALERTS_PER_SCAN=3
ALERT_POST_SPACING_SECONDS=90

FORCE_MARKET_OPEN_FOR_TESTING=false
```

> Keep `FORCE_MARKET_OPEN_FOR_TESTING=false` in production.

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
python3 -m py_compile bot.py src/discord_commands.py src/squeeze_scanner.py
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
│   ├── discord_commands.py   # Slash commands and Discord embeds
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
- Scheduled scan loop
- Public slash commands
- Consolidated admin command
- Docker deployment files

---

## ⚠️ Notes

- SPY, QQQ, and IWM are better treated as market context symbols than normal squeeze candidates.
- ETF short interest should not be expected to reliably come from yfinance.

---

## Disclaimer

This bot is for research, alerts, and personal market monitoring only.

It does not provide financial advice. Validate all signals before trading.

---
