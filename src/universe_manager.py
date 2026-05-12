from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import config
from src.universe_builder import LATEST_UNIVERSE_PATH, build_and_cache_universe


def load_universe() -> dict | None:
    if not LATEST_UNIVERSE_PATH.exists():
        return None

    try:
        return json.loads(LATEST_UNIVERSE_PATH.read_text())

    except json.JSONDecodeError:
        return None


def universe_age_days(universe: dict) -> float | None:
    generated_at = universe.get("generated_at")

    if not generated_at:
        return None

    try:
        generated_dt = datetime.fromisoformat(generated_at)

    except ValueError:
        return None

    now = datetime.now(ZoneInfo(config.MARKET_TIMEZONE))
    age = now - generated_dt

    return round(age.total_seconds() / 86400, 2)


def is_universe_stale(universe: dict | None) -> bool:
    if universe is None:
        return True

    age_days = universe_age_days(universe)

    if age_days is None:
        return True

    return age_days > config.UNIVERSE_MAX_AGE_DAYS


def get_or_build_universe(force_rebuild: bool = False) -> dict:
    """
    Load the cached base universe unless stale or force_rebuild=True.

    Phase 5.6D:
    - force_rebuild lets enriched universe rebuilds also refresh the base
      Nasdaq Trader universe.
    - This prevents an old alphabetically capped base universe from being reused.
    """
    universe = load_universe()

    if force_rebuild or is_universe_stale(universe):
        universe = build_and_cache_universe()

    return universe


def get_universe_summary() -> dict:
    universe = get_or_build_universe()
    age_days = universe_age_days(universe)

    return {
        "generated_at": universe.get("generated_at", "unknown"),
        "source": universe.get("source", "unknown"),
        "count": universe.get("count", len(universe.get("tickers", []))),
        "raw_count": universe.get("raw_count", 0),
        "cleaned_count": universe.get(
            "cleaned_count",
            universe.get("count", 0),
        ),
        "cap_enabled": universe.get("cap_enabled", False),
        "age_days": age_days,
        "min_price": config.MIN_PRICE,
        "min_avg_volume": config.MIN_AVG_VOLUME,
        "max_tickers": config.MAX_TICKERS,
    }