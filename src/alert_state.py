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

EVENT_TYPES = {
    "NEW_DISCOVERY",
    "UPGRADE",
    "DOWNGRADE",
    "SCORE_SURGE",
    "NEW_PEAK_SCORE",
    "STILL_ACTIVE",
    "COOLING_OFF",
    "EXPIRED",
    "REACTIVATED",
}


def _history_path() -> Path:
    return Path(getattr(config, "ALERT_HISTORY_PATH", "cache/alert_history.json"))


def _now_et() -> datetime:
    return datetime.now(ZoneInfo(config.MARKET_TIMEZONE))


def _today_key(now: datetime | None = None) -> str:
    current = now or _now_et()
    return current.date().isoformat()


def _empty_history(now: datetime | None = None) -> dict:
    current = now or _now_et()

    return {
        "trading_date": _today_key(current),
        "tickers": {},
        "updated_at": current.isoformat(),
    }


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


def _payload_is_current_trading_day(payload: dict, now: datetime | None = None) -> bool:
    current = now or _now_et()
    today = _today_key(current)

    trading_date = payload.get("trading_date")

    if trading_date:
        return trading_date == today

    # Backward compatibility for old alert_history.json files.
    updated_at = payload.get("updated_at")

    if updated_at:
        return _same_trading_day(updated_at, current)

    tickers = payload.get("tickers", {})

    for row in tickers.values():
        if _same_trading_day(row.get("last_alert_at"), current):
            return True

    return False


def load_alert_history() -> dict:
    path = _history_path()
    now = _now_et()

    if not path.exists():
        return _empty_history(now)

    try:
        payload = json.loads(path.read_text())

    except json.JSONDecodeError:
        return _empty_history(now)

    if "tickers" not in payload or not isinstance(payload["tickers"], dict):
        payload["tickers"] = {}

    if not _payload_is_current_trading_day(payload, now):
        return {
            "trading_date": _today_key(now),
            "tickers": {},
            "updated_at": now.isoformat(),
            "reset_at": now.isoformat(),
            "previous_trading_date": payload.get("trading_date"),
        }

    payload["trading_date"] = _today_key(now)

    return payload


def save_alert_history(payload: dict) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    payload["trading_date"] = _today_key()
    payload["updated_at"] = _now_et().isoformat()

    path.write_text(json.dumps(payload, indent=2))


def _as_float(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default

        return float(value)

    except (TypeError, ValueError):
        return default


def _as_int(value, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default

        return int(value)

    except (TypeError, ValueError):
        return default


def _get_score(row: dict | None) -> float:
    if not row:
        return 0.0

    return _as_float(
        row.get("current_score", row.get("score", 0)),
        0.0,
    )


def _get_rank(candidate: dict | None, previous: dict | None = None) -> int | None:
    if not candidate:
        return None

    rank = candidate.get("rank")

    if rank is None and previous:
        rank = previous.get("rank")

    return _as_int(rank)


def _get_metric(row: dict | None, key: str) -> float | None:
    if not row:
        return None

    value = row.get(key)

    if value is None:
        return None

    return _as_float(value)


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

    score = _get_score(candidate)
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


def _previous_label(previous: dict | None) -> str | None:
    if not previous:
        return None

    return (
        previous.get("current_classification")
        or previous.get("last_label")
        or get_candidate_label(previous)
    )


def _set_candidate_event(
    candidate: dict,
    event_type: str,
    reason: str,
    previous: dict | None = None,
) -> None:
    """
    Mutates the candidate dict so the later record_candidate_alert() call can save
    the event metadata without changing bot.py yet.
    """
    candidate["_event_type"] = event_type
    candidate["_event_reason"] = reason

    if previous:
        candidate["_previous_score"] = _get_score(previous)
        candidate["_previous_classification"] = _previous_label(previous)
        candidate["_previous_percent_move"] = previous.get("percent_move")
        candidate["_previous_relative_volume"] = previous.get("relative_volume")
        candidate["_peak_score_today"] = previous.get("peak_score_today")


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


def _is_new_peak_score(candidate: dict, previous: dict | None) -> tuple[bool, float]:
    if not previous:
        return False, 0.0

    score = _get_score(candidate)
    peak_score = _as_float(previous.get("peak_score_today"), _get_score(previous))
    delta = score - peak_score

    min_peak_delta = float(getattr(config, "NEW_PEAK_SCORE_MIN_DELTA", 5) or 5)

    return delta >= min_peak_delta, delta


def _minute_cooldown_remaining(previous: dict | None, now: datetime | None = None) -> int:
    """Return remaining alert cooldown minutes when ALERT_COOLDOWN_MODE=minutes."""
    if not previous:
        return 0

    mode = str(getattr(config, "ALERT_COOLDOWN_MODE", "trading_day") or "trading_day").lower()

    if mode not in {"minutes", "minute", "time", "timed"}:
        return 0

    cooldown_minutes = int(getattr(config, "ALERT_COOLDOWN_MINUTES", 0) or 0)

    if cooldown_minutes <= 0:
        return 0

    last_alert_at = _parse_dt(previous.get("last_alert_at"))

    if last_alert_at is None:
        return 0

    current = now or _now_et()
    elapsed_seconds = (current - last_alert_at.astimezone(current.tzinfo)).total_seconds()
    remaining_seconds = cooldown_minutes * 60 - elapsed_seconds

    if remaining_seconds <= 0:
        return 0

    return max(1, int((remaining_seconds + 59) // 60))


def _cooldown_blocks_realert(
    candidate: dict,
    previous: dict | None,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """Block same-strength realerts when the minute cooldown is active.

    New discoveries, upgrades, reactivations, and cooling-off warnings are handled
    outside this helper so important lifecycle events are not suppressed.
    """
    remaining = _minute_cooldown_remaining(previous, now)

    if remaining <= 0:
        return False, None

    symbol = candidate.get("symbol", "ticker")
    return True, f"minute cooldown active for {symbol}: {remaining}m remaining"


def should_post_discovery_alert(
    candidate: dict,
    previous: dict | None,
) -> tuple[bool, str]:
    label = get_candidate_label(candidate)

    if label not in DISCOVERY_LABELS:
        reason = f"{label} is not a discovery alert"
        _set_candidate_event(candidate, "STILL_ACTIVE", reason, previous)
        return False, reason

    symbol = candidate.get("symbol")

    if not symbol:
        reason = "missing symbol"
        _set_candidate_event(candidate, "STILL_ACTIVE", reason, previous)
        return False, reason

    now = _now_et()
    score = _get_score(candidate)

    if previous is None:
        allowed, gate_reason = _new_discovery_allowed(label, candidate)

        if not allowed:
            _set_candidate_event(candidate, "STILL_ACTIVE", gate_reason, previous)
            return False, gate_reason

        reason = f"new {label} discovery"
        _set_candidate_event(candidate, "NEW_DISCOVERY", reason, previous)
        return True, reason

    previous_label = _previous_label(previous) or "Cooling Off"

    previously_alerted_today = _same_trading_day(
        previous.get("last_alert_at"),
        now,
    )

    if not previously_alerted_today:
        allowed, gate_reason = _new_discovery_allowed(label, candidate)

        if not allowed:
            _set_candidate_event(candidate, "STILL_ACTIVE", gate_reason, previous)
            return False, gate_reason

        reason = f"new trading day {label} discovery"
        _set_candidate_event(candidate, "NEW_DISCOVERY", reason, previous)
        return True, reason

    previous_strength = LABEL_STRENGTH.get(previous_label, 0)
    current_strength = LABEL_STRENGTH.get(label, 0)

    if previous_label == "Cooling Off" and label in DISCOVERY_LABELS:
        reason = f"reactivated: Cooling Off → {label}"
        _set_candidate_event(candidate, "REACTIVATED", reason, previous)
        return True, reason

    if current_strength > previous_strength:
        reason = f"conviction upgrade: {previous_label} → {label}"
        _set_candidate_event(candidate, "UPGRADE", reason, previous)
        return True, reason

    if current_strength == previous_strength:
        cooldown_blocked, cooldown_reason = _cooldown_blocks_realert(candidate, previous, now)

        realert_reason = get_realert_reason(candidate, previous)

        if realert_reason:
            if cooldown_blocked:
                _set_candidate_event(candidate, "STILL_ACTIVE", cooldown_reason or "minute cooldown active", previous)
                return False, cooldown_reason or "minute cooldown active"

            reason = f"{label} realert: {realert_reason}"
            _set_candidate_event(candidate, "SCORE_SURGE", reason, previous)
            return True, reason

        is_new_peak, peak_delta = _is_new_peak_score(candidate, previous)

        if is_new_peak:
            if cooldown_blocked:
                _set_candidate_event(candidate, "STILL_ACTIVE", cooldown_reason or "minute cooldown active", previous)
                return False, cooldown_reason or "minute cooldown active"

            reason = (
                f"{label} new peak score: "
                f"{round(score, 2)} "
                f"(+{round(peak_delta, 2)} from prior peak)"
            )
            _set_candidate_event(candidate, "NEW_PEAK_SCORE", reason, previous)
            return True, reason

        reason = f"same-label repeat blocked: {label}"
        _set_candidate_event(candidate, "STILL_ACTIVE", reason, previous)
        return False, reason

    reason = f"weaker label blocked: {previous_label} → {label}"
    _set_candidate_event(candidate, "DOWNGRADE", reason, previous)
    return False, reason


def should_post_cooling_off_alert(
    candidate: dict,
    previous: dict | None,
) -> tuple[bool, str]:
    label = get_candidate_label(candidate)

    if label != "Cooling Off":
        reason = "not cooling off"
        _set_candidate_event(candidate, "STILL_ACTIVE", reason, previous)
        return False, reason

    symbol = candidate.get("symbol")

    if not symbol:
        reason = "missing symbol"
        _set_candidate_event(candidate, "STILL_ACTIVE", reason, previous)
        return False, reason

    if previous is None:
        reason = "cooling off blocked for unseen ticker"
        _set_candidate_event(candidate, "COOLING_OFF", reason, previous)
        return False, reason

    now = _now_et()

    if not _same_trading_day(previous.get("last_alert_at"), now):
        reason = "cooling off blocked because ticker was not alerted today"
        _set_candidate_event(candidate, "COOLING_OFF", reason, previous)
        return False, reason

    if _same_trading_day(previous.get("cooling_off_alerted_at"), now):
        reason = "cooling off already alerted today"
        _set_candidate_event(candidate, "COOLING_OFF", reason, previous)
        return False, reason

    previous_label = _previous_label(previous) or "Cooling Off"

    if previous_label == "Cooling Off":
        reason = "already in cooling off state"
        _set_candidate_event(candidate, "COOLING_OFF", reason, previous)
        return False, reason

    reason = f"cooling off warning: {previous_label} → Cooling Off"
    _set_candidate_event(candidate, "COOLING_OFF", reason, previous)
    return True, reason


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



def record_candidate_seen(
    candidate: dict,
    rank: int,
    previous: dict | None = None,
) -> dict | None:
    """
    Save lightweight per-scan candidate state without counting it as a posted alert.

    Important: bot.py should fetch `previous` before calling this function, then use
    that same pre-update previous row for alert decisions. This keeps setup
    monitoring from accidentally blocking or flattening upgrade/realert logic.
    """
    symbol = candidate.get("symbol")

    if not symbol:
        return None

    symbol = symbol.upper().strip()
    history = load_alert_history()
    now = _now_et().isoformat()
    label = get_candidate_label(candidate)

    stored_previous = history.setdefault("tickers", {}).get(symbol, {})
    prior = previous if previous is not None else stored_previous

    previous_score = _get_score(prior)
    current_score = _get_score(candidate)
    score_change = round(current_score - previous_score, 2)

    previous_label = _previous_label(prior)
    previous_peak_score = _as_float(
        prior.get("peak_score_today") if prior else None,
        previous_score,
    )
    peak_score_today = max(previous_peak_score, current_score)

    previous_peak_label = (
        prior.get("peak_classification_today") if prior else None
    ) or previous_label
    previous_peak_strength = LABEL_STRENGTH.get(previous_peak_label or "Cooling Off", 0)
    current_strength = LABEL_STRENGTH.get(label, 0)

    if current_strength >= previous_peak_strength:
        peak_classification_today = label
    else:
        peak_classification_today = previous_peak_label

    current_event_type = candidate.get("_event_type") or "STILL_ACTIVE"

    # Also attach prior values to the candidate object so scheduled alert embeds
    # and record_candidate_alert() can use the true pre-update state.
    candidate["_previous_score"] = previous_score if prior else None
    candidate["_previous_classification"] = previous_label
    candidate["_previous_percent_move"] = prior.get("percent_move") if prior else None
    candidate["_previous_relative_volume"] = prior.get("relative_volume") if prior else None
    candidate["_peak_score_today"] = peak_score_today

    row = {
        **stored_previous,
        "symbol": symbol,
        "first_seen_at": stored_previous.get("first_seen_at") or now,
        "last_seen_at": now,
        "last_label": label,
        "current_classification": label,
        "previous_classification": previous_label,
        "peak_classification_today": peak_classification_today,
        "current_score": current_score,
        "previous_score": previous_score if prior else None,
        "peak_score_today": peak_score_today,
        "score_change": score_change if prior else None,
        "rank": rank,
        "score": current_score,
        "percent_move": candidate.get("percent_move"),
        "previous_percent_move": prior.get("percent_move") if prior else None,
        "relative_volume": candidate.get("relative_volume"),
        "previous_relative_volume": prior.get("relative_volume") if prior else None,
        "short_float": candidate.get("short_float"),
        "price": candidate.get("price"),
        "latest_volume": candidate.get("latest_volume"),
        "avg_volume_5d": candidate.get("avg_volume_5d"),
        "avg_volume_20d": candidate.get("avg_volume_20d"),
        "tags": candidate.get("tags", []),
        "current_event_type": current_event_type,
        "active_today": label in DISCOVERY_LABELS,
        "cooling_off": label == "Cooling Off",
        "expired": False,
        "missed_scan_count": 0,
    }

    history["tickers"][symbol] = row
    save_alert_history(history)

    return row


def mark_missing_candidates_expired(seen_symbols: set[str]) -> int:
    """
    Quietly mark previously active tickers as expired after they disappear from
    scheduled scan results for multiple scans. This does not post alerts.
    """
    history = load_alert_history()
    tickers = history.setdefault("tickers", {})
    now = _now_et().isoformat()
    expire_after = int(getattr(config, "EXPIRATION_MISSED_SCANS", 2) or 2)
    expired_count = 0

    clean_seen = {symbol.upper().strip() for symbol in seen_symbols if symbol}

    for symbol, row in tickers.items():
        if symbol in clean_seen:
            continue

        if row.get("expired"):
            continue

        if not row.get("active_today"):
            continue

        missed_scan_count = int(row.get("missed_scan_count", 0) or 0) + 1
        row["missed_scan_count"] = missed_scan_count
        row["last_missing_at"] = now

        if missed_scan_count >= expire_after:
            row["expired"] = True
            row["active_today"] = False
            row["cooling_off"] = False
            row["current_event_type"] = "EXPIRED"
            row["last_event_type"] = row.get("last_event_type") or "EXPIRED"
            row["expired_at"] = now
            expired_count += 1

    if expired_count or tickers:
        save_alert_history(history)

    return expired_count

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

    current_score = _get_score(candidate)
    candidate_previous_score = candidate.get("_previous_score")

    if candidate_previous_score is None and previous:
        previous_score = _get_score(previous)
    else:
        previous_score = _as_float(candidate_previous_score)

    score_change = round(current_score - previous_score, 2)

    previous_label = candidate.get("_previous_classification") or _previous_label(previous)
    previous_peak_score = _as_float(
        previous.get("peak_score_today"),
        previous_score,
    )

    peak_score_today = max(previous_peak_score, current_score)

    previous_peak_label = previous.get("peak_classification_today") or previous_label
    previous_peak_strength = LABEL_STRENGTH.get(previous_peak_label or "Cooling Off", 0)
    current_strength = LABEL_STRENGTH.get(label, 0)

    if current_strength >= previous_peak_strength:
        peak_classification_today = label
    else:
        peak_classification_today = previous_peak_label

    event_type = candidate.get("_event_type") or (
        "COOLING_OFF" if alert_type == "cooling_off" else "NEW_DISCOVERY"
    )

    alert_count_today = int(previous.get("alert_count_today", 0) or 0) + 1

    row = {
        **previous,
        "symbol": symbol,
        "first_seen_at": previous.get("first_seen_at") or now,
        "last_seen_at": now,
        "last_alert_at": now,
        "last_label": label,
        "current_classification": label,
        "previous_classification": previous_label,
        "peak_classification_today": peak_classification_today,
        "current_score": current_score,
        "previous_score": previous_score if previous else None,
        "peak_score_today": peak_score_today,
        "score_change": score_change if previous else None,
        "rank": rank,
        "score": current_score,
        "percent_move": candidate.get("percent_move"),
        "previous_percent_move": previous.get("percent_move"),
        "relative_volume": candidate.get("relative_volume"),
        "previous_relative_volume": previous.get("relative_volume"),
        "short_float": candidate.get("short_float"),
        "price": candidate.get("price"),
        "latest_volume": candidate.get("latest_volume"),
        "avg_volume_5d": candidate.get("avg_volume_5d"),
        "avg_volume_20d": candidate.get("avg_volume_20d"),
        "tags": candidate.get("tags", []),
        "reason": reason,
        "last_alert_reason": reason,
        "last_alert_type": alert_type,
        "current_event_type": event_type,
        "last_event_type": event_type,
        "active_today": label in DISCOVERY_LABELS,
        "cooling_off": label == "Cooling Off",
        "expired": False,
        "alert_count_today": alert_count_today,
    }

    if alert_type == "cooling_off" or event_type == "COOLING_OFF":
        row["cooling_off_alerted_at"] = now

    history["tickers"][symbol] = row

    save_alert_history(history)


def get_ticker_alert_state(symbol: str) -> dict | None:
    clean_symbol = symbol.upper().strip()

    if not clean_symbol:
        return None

    history = load_alert_history()
    return history.get("tickers", {}).get(clean_symbol)


def get_daily_setup_recap(limit: int = 5) -> dict:
    """Summarize today's monitored setup state for /squeeze recap."""
    history = load_alert_history()
    rows = list(history.get("tickers", {}).values())

    def score_key(row: dict) -> float:
        return _get_score(row)

    def change_key(row: dict) -> float:
        return _as_float(row.get("score_change"), 0.0)

    def seen_key(row: dict):
        return _parse_dt(row.get("last_seen_at")) or datetime.min.replace(
            tzinfo=ZoneInfo(config.MARKET_TIMEZONE)
        )

    active_rows = [row for row in rows if row.get("active_today") and not row.get("expired")]
    cooling_rows = [row for row in rows if row.get("cooling_off") and not row.get("expired")]
    expired_rows = [row for row in rows if row.get("expired")]
    alerted_rows = [row for row in rows if row.get("last_alert_at")]

    upgrades = [row for row in rows if row.get("current_event_type") == "UPGRADE"]
    score_surges = [row for row in rows if row.get("current_event_type") == "SCORE_SURGE"]
    new_discoveries = [row for row in rows if row.get("current_event_type") == "NEW_DISCOVERY"]

    top_active = sorted(active_rows, key=score_key, reverse=True)[:limit]
    biggest_improvers = sorted(
        [row for row in rows if row.get("score_change") is not None],
        key=change_key,
        reverse=True,
    )[:limit]
    recent_seen = sorted(rows, key=seen_key, reverse=True)[:limit]

    return {
        "trading_date": history.get("trading_date"),
        "total_tracked": len(rows),
        "active_count": len(active_rows),
        "cooling_count": len(cooling_rows),
        "expired_count": len(expired_rows),
        "alerted_count": len(alerted_rows),
        "upgrade_count": len(upgrades),
        "score_surge_count": len(score_surges),
        "new_discovery_count": len(new_discoveries),
        "top_active": top_active,
        "biggest_improvers": biggest_improvers,
        "cooling_off": sorted(cooling_rows, key=score_key, reverse=True)[:limit],
        "expired": sorted(expired_rows, key=seen_key, reverse=True)[:limit],
        "recent_seen": recent_seen,
    }


def clear_all_alert_history() -> None:
    now = _now_et().isoformat()

    save_alert_history(
        {
            "trading_date": _today_key(),
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