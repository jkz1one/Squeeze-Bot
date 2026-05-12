from datetime import datetime, time
from zoneinfo import ZoneInfo

import config


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


def now_et() -> datetime:
    return datetime.now(ZoneInfo(config.MARKET_TIMEZONE))


def is_weekday(dt: datetime | None = None) -> bool:
    current = dt or now_et()
    return current.weekday() < 5


def is_market_open(dt: datetime | None = None) -> bool:
    current = dt or now_et()

    if not is_weekday(current):
        return False

    market_open = _parse_hhmm(config.MARKET_OPEN)
    market_close = _parse_hhmm(config.MARKET_CLOSE)

    current_time = current.time().replace(second=0, microsecond=0)

    return market_open <= current_time <= market_close


def market_status_text(dt: datetime | None = None) -> str:
    current = dt or now_et()

    if is_market_open(current):
        return "OPEN"

    if not is_weekday(current):
        return "CLOSED - Weekend"

    return "CLOSED"