from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json

import config


LABEL_STRENGTH = {
    "Cooling Off": 0,
    "Heating Up": 1,
    "Squeeze Watch": 2,
    "High Conviction": 3,
}

DISCOVERY_LABELS = {
    "Heating Up",
    "Squeeze Watch",
    "High Conviction",
}


def _history_path() -> Path:
    return Path(getattr(config, "ALERT_HISTORY_PATH", "cache/alert_history.json"))


def _now_et() -> datetime:
    return datetime.now(ZoneInfo(config.MARKET_TIMEZONE))


def load_alert_history() -> dict:
    path = _history_path()

    if not path.exists():
        return {"tickers": {}}

    try:
        payload = json.loads(path.read_text())

    except json.JSONDecodeError:
        return {"tickers": {}}

    if "tickers" not in payload or not isinstance(payload["tickers"], dict):
        payload["tickers"] = {}

    return payload


def save_alert_history(payload: dict) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)

    except ValueError:
        return None


def _same_trading_day(value: str | None, now: datetime | None = None) -> bool:
    parsed = _parse_dt(value)

    if parsed is None:
        return False

    current = now or _now_et()
    market_tz = ZoneInfo(config.MARKET_TIMEZONE)

    return parsed.astimezone(market_tz).date() == current.date()


def get_candidate_label(candidate: dict) -> str:
    tags = candidate.get("tags", [])

    if "High Conviction" in tags:
        return "High Conviction"

    if "Squeeze Watch" in tags:
        return "Squeeze Watch"

    if "Heating Up" in tags:
        return "Heating Up"

    if "Cooling Off" in tags:
        return "Cooling Off"

    score = float(candidate.get("score", 0) or 0)
    short_float = candidate.get("short_float")

    if short_float is not None and score >= 80:
        return "High Conviction"

    if short_float is not None and score >= 65:
        return "Squeeze Watch"

    if score >= 45:
        return "Heating Up"

    return "Cooling Off"


def get_previous_candidate_alert(symbol: str | None) -> dict | None:
    if not symbol:
        return None

    history = load_alert_history()
    return history.get("tickers", {}).get(symbol.upper().strip())


def _get_rank(candidate: dict | None, previous: dict | None = None) -> int | None:
    if not candidate:
        return None

    rank = candidate.get("rank")

    if rank is None and previous:
        rank = previous.get("rank")

    try:
        return int(rank)

    except (TypeError, ValueError):
        return None


def _get_score(row: dict | None) -> float:
    if not row:
        return 0.0

    try:
        return float(row.get("score", 0) or 0)

    except (TypeError, ValueError):
        return 0.0


def get_realert_reason(candidate: dict, previous: dict | None) -> str | None:
    if not previous:
        return None

    if not getattr(config, "ENABLE_SCORE_IMPROVEMENT_REALERT", True):
        return None

    score = _get_score(candidate)
    previous_score = _get_score(previous)
    score_delta = score - previous_score

    required_score_delta = float(
        getattr(config, "REALERT_SCORE_IMPROVEMENT", 20) or 0
    )

    if score_delta >= required_score_delta:
        return f"score improved by {round(score_delta, 2)}"

    rank = _get_rank(candidate)
    previous_rank = _get_rank(previous)

    if rank is None or previous_rank is None:
        return None

    rank_delta = previous_rank - rank
    required_rank_delta = int(getattr(config, "REALERT_RANK_IMPROVEMENT", 2) or 0)

    if required_rank_delta > 0 and rank_delta >= required_rank_delta:
        return f"rank improved from #{previous_rank} to #{rank}"

    return None


def _new_discovery_allowed(label: str, candidate: dict) -> tuple[bool, str]:
    rank = _get_rank(candidate)
    score = _get_score(candidate)

    max_rank = int(getattr(config, "DISCOVERY_MAX_RANK", 10) or 10)

    if rank is not None and rank > max_rank:
        return (
            False,
            f"new {label} discovery blocked: rank #{rank} below top {max_rank}",
        )

    if label == "Heating Up":
        min_score = float(
            getattr(config, "HEATING_UP_MIN_DISCOVERY_SCORE", 60) or 60
        )

        if score < min_score:
            return (
                False,
                f"new Heating Up discovery blocked: score {score} below {min_score}",
            )

    return True, f"new {label} discovery passed priority gate"


def should_post_discovery_alert(
    candidate: dict,
    previous: dict | None,
) -> tuple[bool, str]:
    label = get_candidate_label(candidate)

    if label not in DISCOVERY_LABELS:
        return False, f"{label} is not a discovery alert"

    symbol = candidate.get("symbol")

    if not symbol:
        return False, "missing symbol"

    now = _now_et()

    if previous is None:
        allowed, gate_reason = _new_discovery_allowed(label, candidate)

        if not allowed:
            return False, gate_reason

        return True, f"new {label} discovery"

    previous_label = previous.get("last_label") or get_candidate_label(previous)

    previously_alerted_today = _same_trading_day(
        previous.get("last_alert_at"),
        now,
    )

    if not previously_alerted_today:
        allowed, gate_reason = _new_discovery_allowed(label, candidate)

        if not allowed:
            return False, gate_reason

        return True, f"new trading day {label} discovery"

    previous_strength = LABEL_STRENGTH.get(previous_label, 0)
    current_strength = LABEL_STRENGTH.get(label, 0)

    if current_strength > previous_strength:
        return True, f"conviction upgrade: {previous_label} → {label}"

    if current_strength == previous_strength:
        realert_reason = get_realert_reason(candidate, previous)

        if realert_reason:
            return True, f"{label} realert: {realert_reason}"

        return False, f"same-label repeat blocked: {label}"

    return False, f"weaker label blocked: {previous_label} → {label}"


def should_post_cooling_off_alert(
    candidate: dict,
    previous: dict | None,
) -> tuple[bool, str]:
    label = get_candidate_label(candidate)

    if label != "Cooling Off":
        return False, "not cooling off"

    symbol = candidate.get("symbol")

    if not symbol:
        return False, "missing symbol"

    if previous is None:
        return False, "cooling off blocked for unseen ticker"

    now = _now_et()

    if not _same_trading_day(previous.get("last_alert_at"), now):
        return False, "cooling off blocked because ticker was not alerted today"

    if _same_trading_day(previous.get("cooling_off_alerted_at"), now):
        return False, "cooling off already alerted today"

    previous_label = previous.get("last_label") or get_candidate_label(previous)

    if previous_label == "Cooling Off":
        return False, "already in cooling off state"

    return True, f"cooling off warning: {previous_label} → Cooling Off"


def should_alert_candidate(candidate: dict, rank: int) -> tuple[bool, str]:
    """
    Backward-compatible wrapper for older command/loop code.

    New scheduled logic should prefer:
        should_post_discovery_alert()
        should_post_cooling_off_alert()
    """
    candidate = dict(candidate)
    candidate["rank"] = rank

    previous = get_previous_candidate_alert(candidate.get("symbol"))

    should_post, reason = should_post_discovery_alert(candidate, previous)

    if should_post:
        return True, reason

    should_post, reason = should_post_cooling_off_alert(candidate, previous)

    if should_post:
        return True, reason

    return False, reason


def get_recent_alert_history(limit: int = 10) -> list[dict]:
    history = load_alert_history()
    rows = list(history.get("tickers", {}).values())

    def sort_key(row: dict):
        parsed = _parse_dt(row.get("last_alert_at"))
        return parsed or datetime.min.replace(tzinfo=ZoneInfo(config.MARKET_TIMEZONE))

    rows.sort(key=sort_key, reverse=True)

    return rows[:limit]


def record_candidate_alert(
    candidate: dict,
    rank: int,
    reason: str,
    alert_type: str = "discovery",
) -> None:
    symbol = candidate.get("symbol")

    if not symbol:
        return

    symbol = symbol.upper().strip()
    history = load_alert_history()
    now = _now_et().isoformat()
    label = get_candidate_label(candidate)

    previous = history.setdefault("tickers", {}).get(symbol, {})

    row = {
        **previous,
        "symbol": symbol,
        "last_alert_at": now,
        "last_label": label,
        "rank": rank,
        "score": candidate.get("score"),
        "percent_move": candidate.get("percent_move"),
        "relative_volume": candidate.get("relative_volume"),
        "short_float": candidate.get("short_float"),
        "price": candidate.get("price"),
        "tags": candidate.get("tags", []),
        "reason": reason,
        "last_alert_type": alert_type,
    }

    if alert_type == "cooling_off":
        row["cooling_off_alerted_at"] = now

    history["tickers"][symbol] = row
    history["updated_at"] = now

    save_alert_history(history)


def get_ticker_alert_state(symbol: str) -> dict | None:
    clean_symbol = symbol.upper().strip()

    if not clean_symbol:
        return None

    history = load_alert_history()
    return history.get("tickers", {}).get(clean_symbol)


def clear_all_alert_history() -> None:
    now = _now_et().isoformat()

    save_alert_history(
        {
            "tickers": {},
            "updated_at": now,
            "cleared_at": now,
        }
    )


def clear_ticker_alert_state(symbol: str) -> bool:
    clean_symbol = symbol.upper().strip()

    if not clean_symbol:
        return False

    history = load_alert_history()
    tickers = history.setdefault("tickers", {})

    if clean_symbol not in tickers:
        return False

    del tickers[clean_symbol]

    history["updated_at"] = _now_et().isoformat()
    save_alert_history(history)

    return True