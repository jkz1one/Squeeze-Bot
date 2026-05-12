from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import config
from src.universe_enricher import ENRICHED_LATEST_PATH, build_enriched_universe


def load_enriched_universe() -> dict | None:
    if not ENRICHED_LATEST_PATH.exists():
        return None

    try:
        return json.loads(ENRICHED_LATEST_PATH.read_text())

    except json.JSONDecodeError:
        return None


def enriched_universe_age_days(universe: dict) -> float | None:
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


def is_enriched_universe_stale(universe: dict | None) -> bool:
    if universe is None:
        return True

    age_days = enriched_universe_age_days(universe)

    if age_days is None:
        return True

    return age_days > config.UNIVERSE_MAX_AGE_DAYS


def get_or_build_enriched_universe(force_rebuild: bool = False) -> dict:
    universe = load_enriched_universe()

    if force_rebuild or is_enriched_universe_stale(universe):
        universe = build_enriched_universe()

    return universe


def get_enriched_universe_summary(force_rebuild: bool = False) -> dict:
    universe = get_or_build_enriched_universe(force_rebuild=force_rebuild)
    age_days = enriched_universe_age_days(universe)

    return {
        "generated_at": universe.get("generated_at", "unknown"),
        "source": universe.get("source", "unknown"),
        "base_source": universe.get("base_source", "unknown"),
        "base_count": universe.get("base_count", 0),
        "enriched_count": universe.get("enriched_count", 0),
        "passed_count": universe.get("passed_count", len(universe.get("tickers", []))),
        "age_days": age_days,
        "min_price": config.MIN_PRICE,
        "min_avg_volume": config.MIN_AVG_VOLUME,
    }