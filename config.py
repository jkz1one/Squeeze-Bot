import os
from dotenv import load_dotenv

load_dotenv()


def get_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_int(name: str, default: int) -> int:
    value = os.getenv(name)

    if value is None or value == "":
        return default

    return int(value)


def get_float(name: str, default: float) -> float:
    value = os.getenv(name)

    if value is None or value == "":
        return default

    return float(value)


def get_int_list(name: str) -> list[int]:
    value = os.getenv(name, "")

    if not value:
        return []

    ids = []

    for item in value.split(","):
        item = item.strip()

        if item.isdigit():
            ids.append(int(item))

    return ids


DISCORD_ADMIN_IDS = get_int_list("DISCORD_ADMIN_IDS")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
DISCORD_CHANNEL_ID = get_int("DISCORD_CHANNEL_ID", 0)
DISCORD_GUILD_ID = get_int("DISCORD_GUILD_ID", 0)

# Scheduler / scanner timing
SCAN_INTERVAL_MINUTES = get_int("SCAN_INTERVAL_MINUTES", 15)
MIN_SECONDS_BETWEEN_SCANS = get_int("MIN_SECONDS_BETWEEN_SCANS", 300)

# yfinance rate-limit safety
YF_CHUNK_SIZE = get_int("YF_CHUNK_SIZE", 75)
YF_CHUNK_SLEEP_SECONDS = get_float("YF_CHUNK_SLEEP_SECONDS", 1.0)
YF_MAX_RETRIES = get_int("YF_MAX_RETRIES", 2)
YF_RETRY_SLEEP_SECONDS = get_float("YF_RETRY_SLEEP_SECONDS", 5.0)

# Manual scan / top display cap
MAX_ALERTS_PER_SCAN = get_int("MAX_ALERTS_PER_SCAN", 5)

# Scheduled alert posting controls
SCHEDULED_ALERTS_PER_SCAN = get_int("SCHEDULED_ALERTS_PER_SCAN", 2)
ALERT_POST_SPACING_SECONDS = get_int("ALERT_POST_SPACING_SECONDS", 0)

# Scheduled alert priority gate
DISCOVERY_MAX_RANK = get_int("DISCOVERY_MAX_RANK", 10)
HEATING_UP_MIN_DISCOVERY_SCORE = get_float("HEATING_UP_MIN_DISCOVERY_SCORE", 60)

# Market hours
MARKET_TIMEZONE = os.getenv("MARKET_TIMEZONE", "America/New_York")
MARKET_OPEN = os.getenv("MARKET_OPEN", "09:30")
MARKET_CLOSE = os.getenv("MARKET_CLOSE", "16:00")
SCAN_MARKET_HOURS_ONLY = get_bool("SCAN_MARKET_HOURS_ONLY", True)

# Universe filters / freshness
UNIVERSE_MAX_AGE_DAYS = get_int("UNIVERSE_MAX_AGE_DAYS", 1)
MIN_PRICE = get_float("MIN_PRICE", 1.00)
MIN_AVG_VOLUME = get_int("MIN_AVG_VOLUME", 300_000)
MAX_TICKERS = get_int("MAX_TICKERS", 2500)

# Squeeze scanner thresholds
SQUEEZE_SHORT_THRESH = get_float("SQUEEZE_SHORT_THRESH", 0.20)
SQUEEZE_REL_VOL_THRESH = get_float("SQUEEZE_REL_VOL_THRESH", 1.20)
SQUEEZE_PCT_MOVE_THRESH = get_float("SQUEEZE_PCT_MOVE_THRESH", 1.00)

# Alert memory / re-alert behavior
ALERT_COOLDOWN_MODE = os.getenv("ALERT_COOLDOWN_MODE", "trading_day")
ALERT_COOLDOWN_MINUTES = get_int("ALERT_COOLDOWN_MINUTES", 60)

ENABLE_SCORE_IMPROVEMENT_REALERT = get_bool(
    "ENABLE_SCORE_IMPROVEMENT_REALERT",
    True,
)

REALERT_SCORE_IMPROVEMENT = get_float("REALERT_SCORE_IMPROVEMENT", 10)
REALERT_RANK_IMPROVEMENT = get_int("REALERT_RANK_IMPROVEMENT", 2)

ANCHOR_TICKERS = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "AAPL",
    "MSFT",
    "NVDA",
    "TSLA",
    "GME",
    "AMC",
    "KSS",
    "BYND",
    "HIMS",
    "BBAI",
    "GRRR",
    "U",
    "LMND",
]

# DEV ONLY:
# Set true in .env for after-hours testing.
# Set false in production so the scheduled loop respects real market hours.
FORCE_MARKET_OPEN_FOR_TESTING = get_bool("FORCE_MARKET_OPEN_FOR_TESTING", False)

ALERT_HISTORY_PATH = os.getenv("ALERT_HISTORY_PATH", "cache/alert_history.json")