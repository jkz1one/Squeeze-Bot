"""Lightweight Phase 6 explanation helpers for squeeze candidates.

This module is display-only. It does not change scanner rankings, thresholds,
or scheduled alert eligibility. It turns the existing candidate/state fields into
human-readable drivers, risks, and component estimates.
"""

import config


STATUS_STRENGTH = {
    "No Active Signal": 0,
    "Cooling Off": 0,
    "Heating Up": 1,
    "Squeeze Watch": 2,
    "High Conviction": 3,
}


def _as_float(value, default: float | None = 0.0) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_number(value, decimals: int = 2) -> str:
    number = _as_float(value, None)
    if number is None:
        return "N/A"
    if float(number).is_integer():
        return str(int(number))
    return str(round(number, decimals))


def _fmt_pct(value) -> str:
    number = _as_float(value, None)
    if number is None:
        return "N/A"
    return f"{round(number * 100, 1)}%"


def _fmt_signed(value, suffix: str = "", decimals: int = 2) -> str:
    number = _as_float(value, None)
    if number is None:
        return "N/A"
    sign = "+" if number > 0 else ""
    return f"{sign}{round(number, decimals)}{suffix}"


def _candidate_score(candidate: dict) -> float:
    return _as_float(candidate.get("current_score", candidate.get("score", 0)), 0.0) or 0.0


def _candidate_label(candidate: dict) -> str:
    tags = candidate.get("tags", []) or []

    for label in ["High Conviction", "Squeeze Watch", "Heating Up", "Cooling Off"]:
        if label in tags:
            return label

    score = _candidate_score(candidate)
    short_float = candidate.get("short_float")

    if short_float is not None and score >= 80:
        return "High Conviction"
    if short_float is not None and score >= 65:
        return "Squeeze Watch"
    if score >= 45:
        return "Heating Up"
    return "No Active Signal"


def estimate_score_components(candidate: dict, state: dict | None = None) -> dict[str, int]:
    """Return display-only component estimates on a 0-100 scale."""
    row = {**(state or {}), **(candidate or {})}

    score = _candidate_score(row)
    short_float = _as_float(row.get("short_float"), None)
    rel_vol = _as_float(row.get("relative_volume"), 0.0) or 0.0
    pct_move = _as_float(row.get("percent_move"), 0.0) or 0.0
    score_change = _as_float(row.get("score_change"), 0.0) or 0.0

    # Structure = squeeze-prone backdrop. Short float is the main lightweight proxy.
    if short_float is None:
        structure = 45
    else:
        structure = min(100, max(20, int((short_float / max(config.SQUEEZE_SHORT_THRESH, 0.01)) * 70)))

    # Activation = live move + relative volume compared to current thresholds.
    rel_vol_score = min(100, int((rel_vol / max(config.SQUEEZE_REL_VOL_THRESH, 0.01)) * 55))
    move_score = min(100, int((pct_move / max(config.SQUEEZE_PCT_MOVE_THRESH, 0.01)) * 45))
    activation = min(100, rel_vol_score + move_score)

    # Health = current scanner score plus whether it is improving.
    health = int(max(0, min(100, score + max(score_change, 0) * 0.5)))

    # Risk = higher number means more caution, not better quality.
    risk = 20
    if short_float is None:
        risk += 20
    if pct_move >= 12:
        risk += 25
    elif pct_move >= 8:
        risk += 15
    if row.get("cooling_off"):
        risk += 25
    if row.get("expired"):
        risk += 35
    if int(row.get("alert_count_today", 0) or 0) >= 2:
        risk += 10

    return {
        "Structure": int(max(0, min(100, structure))),
        "Activation": int(max(0, min(100, activation))),
        "Health": int(max(0, min(100, health))),
        "Risk": int(max(0, min(100, risk))),
    }


def build_reason_sections(candidate: dict, state: dict | None = None) -> dict[str, list[str] | dict[str, int]]:
    """Build display-ready drivers, risks, notes, and component estimates."""
    row = {**(state or {}), **(candidate or {})}

    drivers: list[str] = []
    risks: list[str] = []
    notes: list[str] = []

    score = _candidate_score(row)
    label = row.get("current_classification") or row.get("last_label") or _candidate_label(row)
    previous_label = row.get("previous_classification")
    previous_score = row.get("previous_score")
    score_change = _as_float(row.get("score_change"), None)
    short_float = _as_float(row.get("short_float"), None)
    rel_vol = _as_float(row.get("relative_volume"), None)
    pct_move = _as_float(row.get("percent_move"), None)
    event_type = row.get("current_event_type") or row.get("last_event_type")

    if short_float is None:
        risks.append("Short float is missing, so structure confidence is lower.")
    elif short_float >= config.SQUEEZE_SHORT_THRESH:
        drivers.append(f"Short float is elevated at {_fmt_pct(short_float)}.")
    else:
        risks.append(f"Short float is below threshold at {_fmt_pct(short_float)}.")

    if rel_vol is not None:
        if rel_vol >= config.SQUEEZE_REL_VOL_THRESH:
            drivers.append(f"Relative volume is elevated at {_fmt_number(rel_vol)}x.")
        else:
            risks.append(f"Relative volume is only {_fmt_number(rel_vol)}x.")

    if pct_move is not None:
        if pct_move >= config.SQUEEZE_PCT_MOVE_THRESH:
            drivers.append(f"Price action is active: {_fmt_signed(pct_move, '%')} move.")
        else:
            risks.append(f"Move is below activation threshold at {_fmt_signed(pct_move, '%')}.")
        if pct_move >= 10:
            risks.append("Move may already be extended intraday.")

    if previous_score is not None and score_change is not None:
        if score_change > 0:
            drivers.append(
                f"Score improved `{_fmt_number(previous_score)} → {_fmt_number(score)}` ({_fmt_signed(score_change)})."
            )
        elif score_change < 0:
            risks.append(
                f"Score faded `{_fmt_number(previous_score)} → {_fmt_number(score)}` ({_fmt_signed(score_change)})."
            )
        else:
            notes.append(f"Score is unchanged at {_fmt_number(score)}.")

    if previous_label and previous_label != label:
        if STATUS_STRENGTH.get(label, 0) > STATUS_STRENGTH.get(previous_label, 0):
            drivers.append(f"Classification upgraded `{previous_label} → {label}`.")
        else:
            risks.append(f"Classification weakened `{previous_label} → {label}`.")

    if event_type == "NEW_DISCOVERY":
        drivers.append("New setup entered the monitored candidate list.")
    elif event_type == "UPGRADE":
        drivers.append("Setup strengthened enough to trigger an upgrade event.")
    elif event_type == "SCORE_SURGE":
        drivers.append("Score surge triggered a realert.")
    elif event_type == "NEW_PEAK_SCORE":
        drivers.append("Setup made a new peak score today.")
    elif event_type == "REACTIVATED":
        drivers.append("Ticker reactivated after cooling off.")
    elif event_type == "COOLING_OFF":
        risks.append("Setup is cooling off versus earlier state.")
    elif event_type == "EXPIRED":
        risks.append("Ticker disappeared from scans long enough to expire quietly.")

    alert_count = int(row.get("alert_count_today", 0) or 0)
    if alert_count >= 2:
        risks.append(f"Already alerted {alert_count} times today.")

    if row.get("expired"):
        risks.append("Expired state is saved for inspection, but no expiration alert was posted.")
    elif row.get("cooling_off"):
        risks.append("Cooling-off flag is active.")
    elif row.get("active_today"):
        notes.append("Still active in today’s monitored setup state.")

    if not drivers:
        drivers.append("No major positive driver found from current lightweight fields.")
    if not risks:
        risks.append("No major risk flag found from current lightweight fields.")

    return {
        "drivers": drivers[:5],
        "risks": risks[:5],
        "notes": notes[:4],
        "components": estimate_score_components(candidate, state),
    }


def format_bullet_block(items: list[str], empty: str = "None") -> str:
    if not items:
        return empty

    text = "\n".join(f"• {item}" for item in items)
    if len(text) <= 1000:
        return text

    return text[:997] + "..."


def format_component_block(components: dict[str, int]) -> str:
    if not components:
        return "N/A"

    return "\n".join(
        f"{name}: `{value}/100`" for name, value in components.items()
    )
