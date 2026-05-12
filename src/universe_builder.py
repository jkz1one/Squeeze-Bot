from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import csv
import io
import json
import re

import requests

import config


CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

LATEST_UNIVERSE_PATH = CACHE_DIR / "universe_latest.json"

NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


BAD_SYMBOL_PATTERNS = (
    r"\$",      # weird special symbols
    r"\^",      # indexes / special cases
    r"/",       # class symbols not normalized
)


BAD_NAME_KEYWORDS = (
    "warrant",
    "warrants",
    "unit",
    "right",
    "rights",
    "preferred",
    "depositary",
    "note",
    "notes",
    "bond",
    "etf",
    "fund",
    "trust preferred",
)


def is_bad_symbol(symbol: str) -> bool:
    if not symbol:
        return True

    if len(symbol) > 6:
        return True

    for pattern in BAD_SYMBOL_PATTERNS:
        if re.search(pattern, symbol):
            return True

    return False


def is_bad_security_name(name: str) -> bool:
    normalized = name.lower()

    return any(keyword in normalized for keyword in BAD_NAME_KEYWORDS)


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.text


def parse_nasdaq_listed(text: str) -> list[dict]:
    rows = []

    reader = csv.DictReader(io.StringIO(text), delimiter="|")

    for row in reader:
        symbol = row.get("Symbol", "").strip().upper()
        name = row.get("Security Name", "").strip()
        test_issue = row.get("Test Issue", "").strip().upper()
        is_etf = row.get("ETF", "").strip().upper()

        if symbol == "File Creation Time":
            continue

        if test_issue == "Y":
            continue

        if is_etf == "Y":
            continue

        if is_bad_symbol(symbol):
            continue

        if is_bad_security_name(name):
            continue

        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "source": "nasdaqlisted",
            }
        )

    return rows


def parse_other_listed(text: str) -> list[dict]:
    rows = []

    reader = csv.DictReader(io.StringIO(text), delimiter="|")

    for row in reader:
        symbol = row.get("ACT Symbol", "").strip().upper()
        name = row.get("Security Name", "").strip()
        test_issue = row.get("Test Issue", "").strip().upper()
        is_etf = row.get("ETF", "").strip().upper()

        if symbol == "File Creation Time":
            continue

        if test_issue == "Y":
            continue

        if is_etf == "Y":
            continue

        if is_bad_symbol(symbol):
            continue

        if is_bad_security_name(name):
            continue

        rows.append(
            {
                "symbol": symbol,
                "name": name,
                "source": "otherlisted",
            }
        )

    return rows


def build_market_universe() -> dict:
    generated_at = datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat()

    nasdaq_text = fetch_text(NASDAQ_LISTED_URL)
    other_text = fetch_text(OTHER_LISTED_URL)

    rows = []
    rows.extend(parse_nasdaq_listed(nasdaq_text))
    rows.extend(parse_other_listed(other_text))

    by_symbol = {}

    for row in rows:
        by_symbol[row["symbol"]] = row

    for symbol in config.ANCHOR_TICKERS:
        clean_symbol = symbol.upper().strip()

        if clean_symbol:
            by_symbol[clean_symbol] = {
                "symbol": clean_symbol,
                "name": "Anchor ticker",
                "source": "anchor_tickers",
            }

    all_symbols = sorted(by_symbol.keys())

    max_tickers = int(getattr(config, "MAX_TICKERS", 0) or 0)

    if max_tickers > 0:
        symbols = all_symbols[:max_tickers]
        cap_enabled = True
    else:
        symbols = all_symbols
        cap_enabled = False

    universe = {
        "generated_at": generated_at,
        "source": "nasdaqtrader_symbol_directory",
        "count": len(symbols),
        "raw_count": len(rows),
        "cleaned_count": len(all_symbols),
        "cap_enabled": cap_enabled,
        "filters": {
            "exclude_test_issues": True,
            "exclude_etfs": True,
            "exclude_warrants_units_rights_preferred_notes": True,
            "min_price": config.MIN_PRICE,
            "min_avg_volume": config.MIN_AVG_VOLUME,
            "max_tickers": max_tickers,
        },
        "tickers": symbols,
        "metadata": {
            symbol: by_symbol[symbol] for symbol in symbols
        },
    }

    return universe

def save_universe(universe: dict) -> None:
    generated_date = universe["generated_at"][:10]
    dated_path = CACHE_DIR / f"universe_{generated_date}.json"

    LATEST_UNIVERSE_PATH.write_text(json.dumps(universe, indent=2))
    dated_path.write_text(json.dumps(universe, indent=2))


def build_and_cache_universe() -> dict:
    universe = build_market_universe()
    save_universe(universe)
    return universe