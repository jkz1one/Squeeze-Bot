from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import time

import yfinance as yf

import config
from src.logger import setup_logger


logger = setup_logger("short_interest", "short_interest.log")

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

SHORT_INTEREST_LATEST_PATH = CACHE_DIR / "short_interest_latest.json"


def load_short_interest_payload() -> dict:
    if not SHORT_INTEREST_LATEST_PATH.exists():
        return {
            "generated_at": None,
            "source": "yfinance_info",
            "data": {},
        }

    try:
        return json.loads(SHORT_INTEREST_LATEST_PATH.read_text())

    except json.JSONDecodeError:
        return {
            "generated_at": None,
            "source": "yfinance_info",
            "data": {},
        }


def save_short_interest_payload(payload: dict) -> None:
    payload["generated_at"] = datetime.now(
        ZoneInfo(config.MARKET_TIMEZONE)
    ).isoformat()

    SHORT_INTEREST_LATEST_PATH.write_text(json.dumps(payload, indent=2))


def normalize_short_float(value) -> float | None:
    if value is None:
        return None

    try:
        short_float = float(value)

    except (TypeError, ValueError):
        return None

    # yfinance normally returns decimal form, e.g. 0.235 = 23.5%.
    # If a source ever returns 23.5, normalize it.
    if short_float > 1:
        short_float = short_float / 100

    if short_float < 0:
        return None

    return round(short_float, 4)


def fetch_short_interest_for_symbol(symbol: str) -> dict:
    clean_symbol = symbol.upper().strip()

    try:
        ticker = yf.Ticker(clean_symbol)
        info = ticker.info or {}

        short_float = normalize_short_float(info.get("shortPercentOfFloat"))
        shares_short = info.get("sharesShort")
        short_ratio = info.get("shortRatio")

        return {
            "symbol": clean_symbol,
            "short_float": short_float,
            "shares_short": shares_short,
            "short_ratio": short_ratio,
            "source": "yfinance_info" if short_float is not None else "missing",
            "fetched_at": datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat(),
        }

    except Exception as exc:
        logger.warning("Failed short interest fetch for %s: %s", clean_symbol, exc)

        return {
            "symbol": clean_symbol,
            "short_float": None,
            "shares_short": None,
            "short_ratio": None,
            "source": "missing",
            "fetched_at": datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat(),
        }


def get_short_interest_for_symbols(
    symbols: list[str],
    force_refresh: bool = False,
    sleep_seconds: float = 0.15,
) -> dict:
    payload = load_short_interest_payload()
    data = payload.get("data", {})

    results = {}

    for index, symbol in enumerate(symbols, start=1):
        clean_symbol = symbol.upper().strip()

        if not clean_symbol:
            continue

        if not force_refresh and clean_symbol in data:
            results[clean_symbol] = data[clean_symbol]
            continue

        logger.info(
            "Fetching short interest %s/%s: %s",
            index,
            len(symbols),
            clean_symbol,
        )

        row = fetch_short_interest_for_symbol(clean_symbol)
        data[clean_symbol] = row
        results[clean_symbol] = row

        time.sleep(sleep_seconds)

    payload["source"] = "yfinance_info"
    payload["data"] = data
    save_short_interest_payload(payload)

    return results


def get_short_interest_for_symbol(symbol: str) -> dict:
    data = get_short_interest_for_symbols([symbol])
    return data.get(
        symbol.upper(),
        {
            "short_float": None,
            "source": "missing",
        },
    )