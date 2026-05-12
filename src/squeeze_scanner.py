from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import time

import yfinance as yf

import config
from src.short_interest_manager import get_short_interest_for_symbols
from src.enriched_universe_manager import get_or_build_enriched_universe
from src.logger import setup_logger


logger = setup_logger("squeeze_scanner", "scanner.log")

CACHE_DIR = Path("cache")
CACHE_DIR.mkdir(exist_ok=True)

SQUEEZE_CANDIDATES_LATEST_PATH = CACHE_DIR / "squeeze_candidates_latest.json"
SCAN_LOCK_PATH = CACHE_DIR / "scan.lock"
SCAN_META_PATH = CACHE_DIR / "squeeze_scan_meta.json"


def chunk_list(items: list[str], chunk_size: int) -> list[list[str]]:
    return [
        items[index : index + chunk_size]
        for index in range(0, len(items), chunk_size)
    ]


def normalize_yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".", "-")


def calculate_percent_move(previous_close: float, last_price: float) -> float:
    if previous_close <= 0:
        return 0.0

    return ((last_price - previous_close) / previous_close) * 100


def bounded_component(value: float, target: float, max_points: float) -> float:
    if target <= 0:
        return 0.0

    return min(value / target, 1.0) * max_points


def load_scan_meta() -> dict:
    if not SCAN_META_PATH.exists():
        return {}

    try:
        return json.loads(SCAN_META_PATH.read_text())

    except json.JSONDecodeError:
        return {}


def save_scan_meta(payload: dict) -> None:
    SCAN_META_PATH.write_text(json.dumps(payload, indent=2))


def seconds_since_last_scan() -> float | None:
    meta = load_scan_meta()
    last_scan_at = meta.get("last_scan_at")

    if not last_scan_at:
        return None

    try:
        last_scan_dt = datetime.fromisoformat(last_scan_at)

    except ValueError:
        return None

    now = datetime.now(ZoneInfo(config.MARKET_TIMEZONE))
    return (now - last_scan_dt).total_seconds()


def get_min_seconds_between_scans() -> int:
    return getattr(config, "MIN_SECONDS_BETWEEN_SCANS", 300)


def can_run_scan(ignore_cooldown: bool = False) -> tuple[bool, str]:
    if SCAN_LOCK_PATH.exists():
        return False, "A scan is already running."

    if ignore_cooldown:
        return True, "OK"

    elapsed = seconds_since_last_scan()
    min_seconds = get_min_seconds_between_scans()

    if elapsed is not None and elapsed < min_seconds:
        wait_seconds = int(min_seconds - elapsed)
        return False, f"Scan cooldown active. Try again in {wait_seconds} seconds."

    return True, "OK"


def write_scan_lock() -> None:
    SCAN_LOCK_PATH.write_text(
        datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat()
    )


def clear_scan_lock() -> None:
    if SCAN_LOCK_PATH.exists():
        SCAN_LOCK_PATH.unlink()


def classify_candidate(
    score: float,
    rel_volume: float,
    percent_move: float,
    short_float: float | None,
) -> list[str]:
    tags = []

    has_short_interest = short_float is not None
    is_short_squeeze_candidate = (
        has_short_interest and short_float >= config.SQUEEZE_SHORT_THRESH
    )

    if is_short_squeeze_candidate and score >= 80:
        tags.append("High Conviction")
    elif is_short_squeeze_candidate and score >= 65:
        tags.append("Squeeze Watch")
    elif score >= 45:
        tags.append("Heating Up")
    else:
        tags.append("Cooling Off")

    if not has_short_interest:
        tags.append("Short Interest Missing")
    else:
        tags.append(f"Short Float {round(short_float * 100, 1)}%")

    if rel_volume >= 2.0:
        tags.append("High Relative Volume")

    if percent_move >= 5.0:
        tags.append("Strong Move")

    return tags


def score_candidate(
    rel_volume: float,
    percent_move: float,
    price: float,
    avg_volume: int,
    short_float: float | None,
) -> float:
    if short_float is None:
        score = 5.0
        score += bounded_component(rel_volume, 5.0, 22)
        score += bounded_component(percent_move, 30.0, 22)
        score += bounded_component(avg_volume, 10_000_000, 7)
        score += bounded_component(price, 20.0, 4)

        return round(min(score, 59), 2)

    score = 5.0
    score += bounded_component(short_float, 0.40, 40)
    score += bounded_component(rel_volume, 5.0, 20)
    score += bounded_component(percent_move, 30.0, 20)
    score += bounded_component(avg_volume, 10_000_000, 10)
    score += bounded_component(price, 20.0, 5)

    if short_float < config.SQUEEZE_SHORT_THRESH:
        score = min(score, 70)

    return round(min(score, 100), 2)


def sort_candidates_by_score(candidates: list[dict]) -> list[dict]:
    """
    Rank source of truth.

    Rank #1 should always be the highest score.
    Tie-breakers are only used when scores are equal.
    """
    return sorted(
        candidates,
        key=lambda item: (
            item.get("score", 0),
            item.get("relative_volume", 0),
            item.get("percent_move", 0),
        ),
        reverse=True,
    )


def normalize_candidate_payload(payload: dict, limit: int | None = None) -> dict:
    """
    Defensive normalization for scanner payloads.

    This keeps:
    - all_candidates sorted by score descending
    - candidates sorted by score descending
    - returned_count aligned with candidates
    - candidate_count aligned with all_candidates

    This prevents cached payloads or future code paths from accidentally
    changing displayed score rank.
    """
    all_candidates = payload.get("all_candidates") or payload.get("candidates", [])
    sorted_all_candidates = sort_candidates_by_score(all_candidates)

    max_results = limit or payload.get("returned_count") or config.MAX_ALERTS_PER_SCAN
    sorted_candidates = sorted_all_candidates[:max_results]

    payload["all_candidates"] = sorted_all_candidates
    payload["candidates"] = sorted_candidates
    payload["candidate_count"] = len(sorted_all_candidates)
    payload["returned_count"] = len(sorted_candidates)

    return payload


def extract_download_series(data, column_name: str, yahoo_symbol: str):
    """
    yfinance can return either normal columns:
        Close, Volume

    or MultiIndex columns:
        ('Close', 'GME')
        ('GME', 'Close')

    This helper always returns a single Series or None.
    """
    if data is None or data.empty:
        return None

    # Normal one-ticker format.
    try:
        if column_name in data.columns:
            value = data[column_name]

            # Sometimes this is still a DataFrame. Pick the matching ticker column
            # if available, otherwise fall back to the first column.
            if hasattr(value, "columns"):
                if yahoo_symbol in value.columns:
                    return value[yahoo_symbol].dropna()

                if len(value.columns) > 0:
                    return value.iloc[:, 0].dropna()

                return None

            return value.dropna()

    except Exception:
        pass

    # MultiIndex format.
    try:
        if hasattr(data.columns, "nlevels") and data.columns.nlevels > 1:
            possible_keys = [
                (column_name, yahoo_symbol),
                (yahoo_symbol, column_name),
            ]

            for key in possible_keys:
                if key in data.columns:
                    return data[key].dropna()

            # Fallback: find any tuple that contains the requested field.
            for key in data.columns:
                if isinstance(key, tuple) and column_name in key:
                    value = data[key]

                    if hasattr(value, "columns"):
                        if len(value.columns) > 0:
                            return value.iloc[:, 0].dropna()

                        return None

                    return value.dropna()

    except Exception:
        pass

    return None


def fetch_scan_data(
    symbols: list[str],
    chunk_size: int | None = None,
    return_meta: bool = False,
):
    results = {}

    resolved_chunk_size = chunk_size or getattr(config, "YF_CHUNK_SIZE", 75)
    chunk_sleep_seconds = getattr(config, "YF_CHUNK_SLEEP_SECONDS", 1.0)
    max_retries = getattr(config, "YF_MAX_RETRIES", 2)
    retry_sleep_seconds = getattr(config, "YF_RETRY_SLEEP_SECONDS", 5.0)

    chunks = chunk_list(symbols, resolved_chunk_size)

    meta = {
        "chunk_size": resolved_chunk_size,
        "chunks_total": len(chunks),
        "chunks_fetched": 0,
        "chunks_failed": 0,
        "failed_chunks": [],
    }

    logger.info(
        "yfinance scan fetch starting. Symbols: %s | Chunks: %s | "
        "Chunk size: %s | Sleep: %ss | Max retries: %s",
        len(symbols),
        len(chunks),
        resolved_chunk_size,
        chunk_sleep_seconds,
        max_retries,
    )

    for index, chunk in enumerate(chunks, start=1):
        yahoo_symbols = [normalize_yahoo_symbol(symbol) for symbol in chunk]
        data = None

        for attempt in range(1, max_retries + 2):
            try:
                logger.info(
                    "Fetching yfinance scan chunk %s/%s. Symbols: %s | "
                    "Attempt: %s/%s",
                    index,
                    len(chunks),
                    len(yahoo_symbols),
                    attempt,
                    max_retries + 1,
                )

                data = yf.download(
                    tickers=" ".join(yahoo_symbols),
                    period="5d",
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=True,
                )

                meta["chunks_fetched"] += 1
                break

            except Exception as exc:
                is_final_attempt = attempt >= max_retries + 1

                logger.warning(
                    "yfinance scan chunk %s/%s failed on attempt %s/%s: %s",
                    index,
                    len(chunks),
                    attempt,
                    max_retries + 1,
                    exc,
                )

                if is_final_attempt:
                    meta["chunks_failed"] += 1
                    meta["failed_chunks"].append(
                        {
                            "chunk_index": index,
                            "symbol_count": len(yahoo_symbols),
                            "first_symbol": chunk[0] if chunk else None,
                            "last_symbol": chunk[-1] if chunk else None,
                            "error": str(exc),
                        }
                    )

                    logger.error(
                        "Skipping failed yfinance scan chunk %s/%s after %s "
                        "attempt(s). First: %s | Last: %s",
                        index,
                        len(chunks),
                        max_retries + 1,
                        chunk[0] if chunk else None,
                        chunk[-1] if chunk else None,
                    )

                    data = None
                    break

                sleep_seconds = retry_sleep_seconds * attempt

                logger.info(
                    "Retrying yfinance scan chunk %s/%s in %s seconds.",
                    index,
                    len(chunks),
                    sleep_seconds,
                )

                time.sleep(sleep_seconds)

        if data is None:
            if index < len(chunks) and chunk_sleep_seconds > 0:
                time.sleep(chunk_sleep_seconds)

            continue

        for original_symbol, yahoo_symbol in zip(chunk, yahoo_symbols):
            try:
                ticker_data = data[yahoo_symbol]

                close_series = ticker_data["Close"].dropna()
                volume_series = ticker_data["Volume"].dropna()

                if len(close_series) < 2 or volume_series.empty:
                    continue

                last_price = float(close_series.iloc[-1])
                previous_close = float(close_series.iloc[-2])
                latest_volume = int(volume_series.iloc[-1])
                avg_volume_5d = int(volume_series.tail(5).mean())

                if previous_close <= 0 or avg_volume_5d <= 0:
                    continue

                percent_move = calculate_percent_move(previous_close, last_price)
                rel_volume = latest_volume / avg_volume_5d

                results[original_symbol] = {
                    "symbol": original_symbol,
                    "price": round(last_price, 2),
                    "previous_close": round(previous_close, 2),
                    "percent_move": round(percent_move, 2),
                    "latest_volume": latest_volume,
                    "avg_volume_5d": avg_volume_5d,
                    "relative_volume": round(rel_volume, 2),
                }

            except Exception:
                continue

        logger.info(
            "Completed yfinance scan chunk %s/%s. Running scanned symbols: %s",
            index,
            len(chunks),
            len(results),
        )

        if index < len(chunks) and chunk_sleep_seconds > 0:
            time.sleep(chunk_sleep_seconds)

    logger.info(
        "yfinance scan fetch complete. Results: %s | Chunks fetched: %s/%s | "
        "Failed chunks: %s",
        len(results),
        meta["chunks_fetched"],
        meta["chunks_total"],
        meta["chunks_failed"],
    )

    if return_meta:
        return results, meta

    return results


def get_preliminary_candidates(scan_data: dict, enriched_data: dict) -> list[dict]:
    preliminary = []

    for symbol, row in scan_data.items():
        enriched_row = enriched_data.get(symbol, {})

        price = row.get("price", 0)
        avg_volume_20d = enriched_row.get("avg_volume_20d", 0)
        rel_volume = row.get("relative_volume", 0)
        percent_move = row.get("percent_move", 0)

        if price < config.MIN_PRICE:
            continue

        if avg_volume_20d < config.MIN_AVG_VOLUME:
            continue

        if rel_volume < config.SQUEEZE_REL_VOL_THRESH:
            continue

        if percent_move < config.SQUEEZE_PCT_MOVE_THRESH:
            continue

        preliminary.append(
            {
                "symbol": symbol,
                "price": price,
                "percent_move": percent_move,
                "relative_volume": rel_volume,
                "latest_volume": row.get("latest_volume", 0),
                "avg_volume_5d": row.get("avg_volume_5d", 0),
                "avg_volume_20d": avg_volume_20d,
            }
        )

    return preliminary


def build_candidates(scan_data: dict, enriched_data: dict) -> list[dict]:
    preliminary = get_preliminary_candidates(scan_data, enriched_data)
    symbols = [row["symbol"] for row in preliminary]

    logger.info(
        "Preliminary candidates passed momentum filters: %s",
        len(preliminary),
    )

    short_interest_map = get_short_interest_for_symbols(symbols)

    candidates = []

    for row in preliminary:
        symbol = row["symbol"]

        short_interest = short_interest_map.get(symbol, {})
        short_float = short_interest.get("short_float")
        short_interest_source = short_interest.get("source", "missing")
        shares_short = short_interest.get("shares_short")
        short_ratio = short_interest.get("short_ratio")

        score = score_candidate(
            rel_volume=row["relative_volume"],
            percent_move=row["percent_move"],
            price=row["price"],
            avg_volume=row["avg_volume_20d"],
            short_float=short_float,
        )

        candidates.append(
            {
                "symbol": symbol,
                "score": score,
                "tags": classify_candidate(
                    score=score,
                    rel_volume=row["relative_volume"],
                    percent_move=row["percent_move"],
                    short_float=short_float,
                ),
                "price": row["price"],
                "percent_move": row["percent_move"],
                "relative_volume": row["relative_volume"],
                "latest_volume": row["latest_volume"],
                "avg_volume_5d": row["avg_volume_5d"],
                "avg_volume_20d": row["avg_volume_20d"],
                "short_float": short_float,
                "shares_short": shares_short,
                "short_ratio": short_ratio,
                "short_interest_source": short_interest_source,
            }
        )

    return sort_candidates_by_score(candidates)


def save_candidates(payload: dict) -> None:
    generated_date = payload["generated_at"][:10]
    dated_path = CACHE_DIR / f"squeeze_candidates_{generated_date}.json"

    SQUEEZE_CANDIDATES_LATEST_PATH.write_text(json.dumps(payload, indent=2))
    dated_path.write_text(json.dumps(payload, indent=2))


def build_skip_payload(reason: str) -> dict:
    cached = load_latest_candidates()

    if cached is not None:
        cached["scan_skipped"] = True
        cached["skip_reason"] = reason
        return cached

    return {
        "generated_at": datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat(),
        "source": "scan_skipped",
        "scan_skipped": True,
        "skip_reason": reason,
        "universe_count": 0,
        "scan_universe_count": 0,
        "scanned_count": 0,
        "candidate_count": 0,
        "returned_count": 0,
        "short_interest_connected_count": 0,
        "scan_duration_seconds": 0,
        "chunk_size": getattr(config, "YF_CHUNK_SIZE", 75),
        "chunks_total": 0,
        "chunks_fetched": 0,
        "chunks_failed": 0,
        "failed_chunks": [],
        "filters": {
            "min_price": config.MIN_PRICE,
            "min_avg_volume": config.MIN_AVG_VOLUME,
            "rel_volume_threshold": config.SQUEEZE_REL_VOL_THRESH,
            "percent_move_threshold": config.SQUEEZE_PCT_MOVE_THRESH,
            "short_float_threshold": config.SQUEEZE_SHORT_THRESH,
        },
        "candidates": [],
        "all_candidates": [],
    }


def run_squeeze_scan(limit: int | None = None, force: bool = False) -> dict:
    allowed, reason = can_run_scan(ignore_cooldown=force)

    if not allowed:
        logger.info("Scan skipped: %s", reason)
        return build_skip_payload(reason)

    write_scan_lock()
    scan_started_at = time.perf_counter()

    try:
        enriched_universe = get_or_build_enriched_universe()
        symbols = enriched_universe.get("tickers", [])
        enriched_data = enriched_universe.get("data", {})

        generated_at = datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat()

        logger.info("Loaded enriched universe count: %s", len(symbols))
        logger.info("Scan universe count: %s", len(symbols))

        scan_data, scan_fetch_meta = fetch_scan_data(
            symbols,
            chunk_size=getattr(config, "YF_CHUNK_SIZE", 75),
            return_meta=True,
        )

        logger.info(
            "yfinance chunks fetched: %s/%s | Failed: %s | Chunk size: %s",
            scan_fetch_meta.get("chunks_fetched", 0),
            scan_fetch_meta.get("chunks_total", 0),
            scan_fetch_meta.get("chunks_failed", 0),
            scan_fetch_meta.get("chunk_size", getattr(config, "YF_CHUNK_SIZE", 75)),
        )

        candidates = build_candidates(scan_data, enriched_data)

        max_results = limit or config.MAX_ALERTS_PER_SCAN
        candidates = sort_candidates_by_score(candidates)
        top_candidates = candidates[:max_results]

        short_interest_connected_count = sum(
            1 for candidate in candidates if candidate.get("short_float") is not None
        )

        scan_duration_seconds = round(time.perf_counter() - scan_started_at, 2)

        payload = {
            "generated_at": generated_at,
            "source": "yfinance_candidate_scanner",
            "scan_skipped": False,
            "universe_count": len(symbols),
            "scan_universe_count": len(symbols),
            "scanned_count": len(scan_data),
            "candidate_count": len(candidates),
            "returned_count": len(top_candidates),
            "short_interest_connected_count": short_interest_connected_count,
            "scan_duration_seconds": scan_duration_seconds,
            "chunk_size": scan_fetch_meta.get(
                "chunk_size",
                getattr(config, "YF_CHUNK_SIZE", 75),
            ),
            "chunks_total": scan_fetch_meta.get("chunks_total", 0),
            "chunks_fetched": scan_fetch_meta.get("chunks_fetched", 0),
            "chunks_failed": scan_fetch_meta.get("chunks_failed", 0),
            "failed_chunks": scan_fetch_meta.get("failed_chunks", []),
            "filters": {
                "min_price": config.MIN_PRICE,
                "min_avg_volume": config.MIN_AVG_VOLUME,
                "rel_volume_threshold": config.SQUEEZE_REL_VOL_THRESH,
                "percent_move_threshold": config.SQUEEZE_PCT_MOVE_THRESH,
                "short_float_threshold": config.SQUEEZE_SHORT_THRESH,
            },
            "candidates": top_candidates,
            "all_candidates": candidates,
        }

        payload = normalize_candidate_payload(payload, limit=max_results)

        save_candidates(payload)

        save_scan_meta(
            {
                "last_scan_at": generated_at,
                "universe_count": len(symbols),
                "scan_universe_count": len(symbols),
                "scanned_count": len(scan_data),
                "candidate_count": len(candidates),
                "short_interest_connected_count": short_interest_connected_count,
                "scan_duration_seconds": scan_duration_seconds,
                "chunk_size": payload.get("chunk_size"),
                "chunks_total": payload.get("chunks_total"),
                "chunks_fetched": payload.get("chunks_fetched"),
                "chunks_failed": payload.get("chunks_failed"),
            }
        )

        logger.info("Scan duration: %s seconds", scan_duration_seconds)

        logger.info(
            "Candidate count: %s | Returned: %s | SI connected: %s",
            len(candidates),
            len(top_candidates),
            short_interest_connected_count,
        )

        logger.info(
            "Squeeze scan complete. Universe: %s | Scan universe: %s | "
            "Scanned: %s | Candidates: %s | Duration: %ss",
            len(symbols),
            len(symbols),
            len(scan_data),
            len(candidates),
            scan_duration_seconds,
        )

        return payload

    finally:
        clear_scan_lock()


def load_latest_candidates() -> dict | None:
    if not SQUEEZE_CANDIDATES_LATEST_PATH.exists():
        return None

    try:
        payload = json.loads(SQUEEZE_CANDIDATES_LATEST_PATH.read_text())
        return normalize_candidate_payload(payload)

    except json.JSONDecodeError:
        return None


def get_top_candidates(force_scan: bool = False, limit: int | None = None) -> dict:
    if force_scan:
        return run_squeeze_scan(limit=limit, force=True)

    cached = load_latest_candidates()

    if cached is None:
        return run_squeeze_scan(limit=limit)

    if limit is not None:
        return normalize_candidate_payload(cached, limit=limit)

    return normalize_candidate_payload(cached)


def refresh_missing_short_interest_for_latest_candidates(
    limit: int | None = None,
    sleep_seconds: float = 0.15,
    symbol: str | None = None,
) -> dict:
    """
    Phase 5.8 — Short Interest Missing Recovery.

    Refresh short interest only for cached candidates.

    Default behavior:
    - Refreshes only cached candidates where short_float is missing.

    Symbol behavior:
    - If symbol is provided, refreshes short interest only for that cached ticker,
      whether short_float is currently missing or already present.

    Does NOT run a full squeeze scan.
    Does NOT fetch full yfinance price/volume chunks.
    Reuses existing score_candidate(), classify_candidate(), and sorting logic.
    """
    if SCAN_LOCK_PATH.exists():
        return {
            "success": False,
            "skipped": True,
            "reason": "A scan is already running. Try again after it finishes.",
            "missing_before": 0,
            "attempted": 0,
            "recovered": 0,
            "still_missing": 0,
            "candidates_rescored": 0,
            "top_cache_updated": False,
        }

    payload = load_latest_candidates()

    if payload is None:
        return {
            "success": False,
            "skipped": True,
            "reason": "No cached squeeze candidates found.",
            "missing_before": 0,
            "attempted": 0,
            "recovered": 0,
            "still_missing": 0,
            "candidates_rescored": 0,
            "top_cache_updated": False,
        }

    all_candidates = payload.get("all_candidates") or payload.get("candidates", [])

    if not all_candidates:
        return {
            "success": False,
            "skipped": True,
            "reason": "Cached candidate file exists, but contains no candidates.",
            "missing_before": 0,
            "attempted": 0,
            "recovered": 0,
            "still_missing": 0,
            "candidates_rescored": 0,
            "top_cache_updated": False,
        }

    clean_symbol = symbol.upper().strip() if symbol else None
    target_candidates = all_candidates

    if clean_symbol:
        target_candidates = [
            candidate
            for candidate in all_candidates
            if str(candidate.get("symbol", "")).upper().strip() == clean_symbol
        ]

        if not target_candidates:
            return {
                "success": False,
                "skipped": True,
                "reason": f"{clean_symbol} was not found in cached candidates.",
                "missing_before": 0,
                "attempted": 0,
                "recovered": 0,
                "still_missing": 0,
                "candidates_rescored": 0,
                "top_cache_updated": False,
            }

    missing_candidates = [
        candidate
        for candidate in target_candidates
        if candidate.get("short_float") is None
    ]

    missing_before = len(missing_candidates)

    if clean_symbol:
        refresh_symbols = [clean_symbol]
        originally_missing_symbols = {
            clean_symbol
            for candidate in target_candidates
            if candidate.get("short_float") is None
        }
    else:
        refresh_symbols = sorted(
            {
                str(candidate.get("symbol", "")).upper().strip()
                for candidate in missing_candidates
                if candidate.get("symbol")
            }
        )
        originally_missing_symbols = set(refresh_symbols)

    short_interest_map = {}

    if refresh_symbols:
        logger.info(
            "Refreshing short interest for %s cached candidate(s): %s",
            len(refresh_symbols),
            ", ".join(refresh_symbols),
        )

        short_interest_map = get_short_interest_for_symbols(
            refresh_symbols,
            force_refresh=True,
            sleep_seconds=sleep_seconds,
        )

    for candidate in all_candidates:
        candidate_symbol = str(candidate.get("symbol", "")).upper().strip()

        if candidate_symbol not in short_interest_map:
            continue

        short_interest = short_interest_map.get(candidate_symbol, {})

        candidate["short_float"] = short_interest.get("short_float")
        candidate["shares_short"] = short_interest.get("shares_short")
        candidate["short_ratio"] = short_interest.get("short_ratio")
        candidate["short_interest_source"] = short_interest.get("source", "missing")

    recovered_symbols = set()
    still_missing_symbols = set()

    if clean_symbol:
        evaluated_symbols = set(refresh_symbols)
    else:
        evaluated_symbols = originally_missing_symbols

    for candidate in all_candidates:
        candidate_symbol = str(candidate.get("symbol", "")).upper().strip()

        if candidate_symbol not in evaluated_symbols:
            continue

        if candidate.get("short_float") is not None:
            recovered_symbols.add(candidate_symbol)
        else:
            still_missing_symbols.add(candidate_symbol)

    for candidate in all_candidates:
        rel_volume = float(candidate.get("relative_volume", 0) or 0)
        percent_move = float(candidate.get("percent_move", 0) or 0)
        price = float(candidate.get("price", 0) or 0)
        avg_volume = int(
            candidate.get("avg_volume_20d")
            or candidate.get("avg_volume_5d")
            or 0
        )
        short_float = candidate.get("short_float")

        score = score_candidate(
            rel_volume=rel_volume,
            percent_move=percent_move,
            price=price,
            avg_volume=avg_volume,
            short_float=short_float,
        )

        candidate["score"] = score
        candidate["tags"] = classify_candidate(
            score=score,
            rel_volume=rel_volume,
            percent_move=percent_move,
            short_float=short_float,
        )

    short_interest_connected_count = sum(
        1 for candidate in all_candidates if candidate.get("short_float") is not None
    )

    refreshed_at = datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat()

    payload["all_candidates"] = all_candidates
    payload["short_interest_connected_count"] = short_interest_connected_count
    payload["short_interest_refreshed_at"] = refreshed_at
    payload["short_interest_refresh"] = {
        "refreshed_at": refreshed_at,
        "symbol": clean_symbol,
        "missing_before": missing_before,
        "attempted": len(refresh_symbols),
        "recovered": len(recovered_symbols),
        "still_missing": len(still_missing_symbols),
        "recovered_symbols": sorted(recovered_symbols),
        "still_missing_symbols": sorted(still_missing_symbols),
    }

    payload = normalize_candidate_payload(
        payload,
        limit=limit or payload.get("returned_count") or config.MAX_ALERTS_PER_SCAN,
    )

    save_candidates(payload)

    logger.info(
        "Short interest recovery complete. Symbol: %s | Missing before: %s | "
        "Attempted: %s | Recovered: %s | Still missing: %s | Re-scored: %s",
        clean_symbol or "ALL_MISSING",
        missing_before,
        len(refresh_symbols),
        len(recovered_symbols),
        len(still_missing_symbols),
        len(all_candidates),
    )

    if clean_symbol:
        reason = f"Short-interest refresh complete for {clean_symbol}."
    else:
        reason = "Short-interest recovery complete."

    return {
        "success": True,
        "skipped": False,
        "reason": reason,
        "symbol": clean_symbol,
        "missing_before": missing_before,
        "attempted": len(refresh_symbols),
        "recovered": len(recovered_symbols),
        "still_missing": len(still_missing_symbols),
        "candidates_rescored": len(all_candidates),
        "top_cache_updated": True,
        "short_interest_connected_count": short_interest_connected_count,
        "refreshed_at": refreshed_at,
        "recovered_symbols": sorted(recovered_symbols),
        "still_missing_symbols": sorted(still_missing_symbols),
    }


def build_single_ticker_report(symbol: str) -> dict:
    clean_symbol = symbol.upper().strip()
    yahoo_symbol = normalize_yahoo_symbol(clean_symbol)

    generated_at = datetime.now(ZoneInfo(config.MARKET_TIMEZONE)).isoformat()

    if not clean_symbol:
        return {
            "generated_at": generated_at,
            "source": "single_ticker_report",
            "symbol": clean_symbol,
            "found": False,
            "error": "Missing ticker symbol.",
            "candidate": None,
            "candidates": [],
            "all_candidates": [],
        }

    try:
        data = yf.download(
            tickers=yahoo_symbol,
            period="20d",
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )

    except Exception as exc:
        logger.error("Single ticker report failed for %s: %s", clean_symbol, exc)

        return {
            "generated_at": generated_at,
            "source": "single_ticker_report",
            "symbol": clean_symbol,
            "found": False,
            "error": f"Failed to fetch market data for {clean_symbol}.",
            "candidate": None,
            "candidates": [],
            "all_candidates": [],
        }

    if data is None or data.empty:
        return {
            "generated_at": generated_at,
            "source": "single_ticker_report",
            "symbol": clean_symbol,
            "found": False,
            "error": "No market data returned.",
            "candidate": None,
            "candidates": [],
            "all_candidates": [],
        }

    try:
        close_series = extract_download_series(data, "Close", yahoo_symbol)
        volume_series = extract_download_series(data, "Volume", yahoo_symbol)

        if close_series is None or volume_series is None:
            return {
                "generated_at": generated_at,
                "source": "single_ticker_report",
                "symbol": clean_symbol,
                "found": False,
                "error": "Could not read close or volume data from yfinance response.",
                "candidate": None,
                "candidates": [],
                "all_candidates": [],
            }

        close_series = close_series.dropna()
        volume_series = volume_series.dropna()

        if len(close_series) < 2 or len(volume_series) < 2:
            return {
                "generated_at": generated_at,
                "source": "single_ticker_report",
                "symbol": clean_symbol,
                "found": False,
                "error": "Not enough price or volume history.",
                "candidate": None,
                "candidates": [],
                "all_candidates": [],
            }

        last_price = float(close_series.iloc[-1])
        previous_close = float(close_series.iloc[-2])
        latest_volume = int(volume_series.iloc[-1])
        avg_volume_5d = int(volume_series.tail(5).mean())
        avg_volume_20d = int(volume_series.tail(20).mean())

        if previous_close <= 0 or avg_volume_5d <= 0:
            return {
                "generated_at": generated_at,
                "source": "single_ticker_report",
                "symbol": clean_symbol,
                "found": False,
                "error": "Invalid price or volume data returned.",
                "candidate": None,
                "candidates": [],
                "all_candidates": [],
            }

        percent_move = round(
            calculate_percent_move(previous_close, last_price),
            2,
        )

        relative_volume = round(latest_volume / avg_volume_5d, 2)

    except Exception as exc:
        logger.error("Failed parsing single ticker data for %s: %s", clean_symbol, exc)

        return {
            "generated_at": generated_at,
            "source": "single_ticker_report",
            "symbol": clean_symbol,
            "found": False,
            "error": "Failed to parse market data from yfinance response.",
            "candidate": None,
            "candidates": [],
            "all_candidates": [],
        }

    short_interest_map = get_short_interest_for_symbols(
        [clean_symbol],
        force_refresh=True,
        sleep_seconds=0.15,
    )

    short_interest = short_interest_map.get(clean_symbol, {})

    short_float = short_interest.get("short_float")
    short_interest_source = short_interest.get("source", "missing")
    shares_short = short_interest.get("shares_short")
    short_ratio = short_interest.get("short_ratio")

    score = score_candidate(
        rel_volume=relative_volume,
        percent_move=percent_move,
        price=last_price,
        avg_volume=avg_volume_20d,
        short_float=short_float,
    )

    candidate = {
        "symbol": clean_symbol,
        "score": score,
        "tags": classify_candidate(
            score=score,
            rel_volume=relative_volume,
            percent_move=percent_move,
            short_float=short_float,
        ),
        "price": round(last_price, 2),
        "previous_close": round(previous_close, 2),
        "percent_move": percent_move,
        "relative_volume": relative_volume,
        "latest_volume": latest_volume,
        "avg_volume_5d": avg_volume_5d,
        "avg_volume_20d": avg_volume_20d,
        "short_float": short_float,
        "shares_short": shares_short,
        "short_ratio": short_ratio,
        "short_interest_source": short_interest_source,
    }

    failed_reasons = []

    if candidate["price"] < config.MIN_PRICE:
        failed_reasons.append(
            f"Price below minimum: ${candidate['price']} < ${config.MIN_PRICE}"
        )

    if avg_volume_20d < config.MIN_AVG_VOLUME:
        failed_reasons.append(
            f"Average volume below minimum: "
            f"{avg_volume_20d:,} < {config.MIN_AVG_VOLUME:,}"
        )

    if relative_volume < config.SQUEEZE_REL_VOL_THRESH:
        failed_reasons.append(
            f"Relative volume below threshold: "
            f"{relative_volume}x < {config.SQUEEZE_REL_VOL_THRESH}x"
        )

    if percent_move < config.SQUEEZE_PCT_MOVE_THRESH:
        failed_reasons.append(
            f"Percent move below threshold: "
            f"{percent_move}% < {config.SQUEEZE_PCT_MOVE_THRESH}%"
        )

    passed_filters = len(failed_reasons) == 0

    payload = {
        "generated_at": generated_at,
        "source": "single_ticker_report",
        "symbol": clean_symbol,
        "found": True,
        "passed_filters": passed_filters,
        "failed_reasons": failed_reasons,
        "scan_skipped": False,
        "universe_count": 1,
        "scan_universe_count": 1,
        "scanned_count": 1,
        "candidate_count": 1 if passed_filters else 0,
        "returned_count": 1,
        "short_interest_connected_count": 1 if short_float is not None else 0,
        "filters": {
            "min_price": config.MIN_PRICE,
            "min_avg_volume": config.MIN_AVG_VOLUME,
            "rel_volume_threshold": config.SQUEEZE_REL_VOL_THRESH,
            "percent_move_threshold": config.SQUEEZE_PCT_MOVE_THRESH,
            "short_float_threshold": config.SQUEEZE_SHORT_THRESH,
        },
        "candidate": candidate,
        "candidates": [candidate] if passed_filters else [],
        "all_candidates": [candidate],
    }

    return normalize_candidate_payload(payload, limit=1)


if __name__ == "__main__":
    run_squeeze_scan()