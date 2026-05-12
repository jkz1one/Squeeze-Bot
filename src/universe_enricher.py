from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import time

import yfinance as yf

import config
from src.universe_manager import get_or_build_universe


CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

ENRICHED_LATEST_PATH = CACHE_DIR / "universe_enriched_latest.json"


def chunk_list(items: list[str], chunk_size: int) -> list[list[str]]:
    return [
        items[index : index + chunk_size]
        for index in range(0, len(items), chunk_size)
    ]


def normalize_yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def enrich_symbols(symbols: list[str], chunk_size: int = 100) -> dict:
    enriched = {}
    chunks = chunk_list(symbols, chunk_size)

    for index, chunk in enumerate(chunks, start=1):
        yahoo_symbols = [normalize_yahoo_symbol(symbol) for symbol in chunk]

        print(
            f"Fetching enrichment chunk {index}/{len(chunks)}: "
            f"{len(yahoo_symbols)} symbols"
        )

        try:
            data = yf.download(
                tickers=" ".join(yahoo_symbols),
                period="20d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                progress=False,
                threads=True,
            )

        except Exception as exc:
            print(f"Failed enrichment chunk {index}: {exc}")
            continue

        for original_symbol, yahoo_symbol in zip(chunk, yahoo_symbols):
            try:
                ticker_data = data[yahoo_symbol]

                close_series = ticker_data["Close"].dropna()
                volume_series = ticker_data["Volume"].dropna()

                if close_series.empty or volume_series.empty:
                    continue

                last_price = float(close_series.iloc[-1])
                avg_volume = int(volume_series.tail(20).mean())

                enriched[original_symbol] = {
                    "symbol": original_symbol,
                    "price": round(last_price, 2),
                    "avg_volume_20d": avg_volume,
                }

            except Exception:
                continue

        time.sleep(0.5)

    return enriched


def apply_universe_filters(enriched: dict) -> list[dict]:
    passed = []

    for symbol, row in enriched.items():
        price = row.get("price", 0)
        avg_volume = row.get("avg_volume_20d", 0)

        if price < config.MIN_PRICE:
            continue

        if avg_volume < config.MIN_AVG_VOLUME:
            continue

        passed.append(row)

    return sorted(passed, key=lambda item: item["symbol"])


def save_enriched_universe(payload: dict) -> None:
    generated_date = payload["generated_at"][:10]
    dated_path = CACHE_DIR / f"universe_enriched_{generated_date}.json"

    ENRICHED_LATEST_PATH.write_text(json.dumps(payload, indent=2))
    dated_path.write_text(json.dumps(payload, indent=2))


def build_enriched_universe(force_base_rebuild: bool = False) -> dict:
    """
    Build the enriched universe from the base universe.

    Phase 5.6D note:
    - The base universe should no longer be alphabetically capped before enrichment.
    - If force_base_rebuild=True, the base Nasdaq universe is rebuilt first.
    - Price and average-volume filters are applied only after enrichment.
    """
    base_universe = get_or_build_universe(force_rebuild=force_base_rebuild)
    base_symbols = base_universe.get("tickers", [])

    generated_at = datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat()

    print(f"Starting enrichment for {len(base_symbols)} symbols...")

    enriched = enrich_symbols(base_symbols)
    passed = apply_universe_filters(enriched)

    payload = {
        "generated_at": generated_at,
        "source": "yfinance_enriched_universe",
        "base_source": base_universe.get("source", "unknown"),
        "base_count": len(base_symbols),
        "base_raw_count": base_universe.get("raw_count", 0),
        "base_cleaned_count": base_universe.get(
            "cleaned_count",
            len(base_symbols),
        ),
        "base_cap_enabled": base_universe.get("cap_enabled", False),
        "enriched_count": len(enriched),
        "passed_count": len(passed),
        "filters": {
            "min_price": config.MIN_PRICE,
            "min_avg_volume": config.MIN_AVG_VOLUME,
        },
        "tickers": [row["symbol"] for row in passed],
        "data": {
            row["symbol"]: row for row in passed
        },
    }

    save_enriched_universe(payload)

    print(
        f"Enrichment complete. "
        f"Base: {len(base_symbols)} | "
        f"Enriched: {len(enriched)} | "
        f"Passed: {len(passed)} | "
        f"Base cap enabled: {payload['base_cap_enabled']}"
    )

    return payload


if __name__ == "__main__":
    build_enriched_universe(force_base_rebuild=True)