# Discord Squeeze Bot

Lightweight Discord bot for short squeeze alerts.

## Phase 1 Status

Current scaffold supports:

- `.env` config loading
- Discord bot startup
- Basic logging
- Scheduled loop placeholder
- Basic `!squeeze` commands

Scanner logic is not implemented yet.

## Setup

```bash
python -m venv venv
source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env