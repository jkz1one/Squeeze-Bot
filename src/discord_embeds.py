import discord
from datetime import datetime
from zoneinfo import ZoneInfo

import config


def format_generated_at(value: str | None) -> str:
    """Format raw ISO cache timestamps into a Discord-friendly ET timestamp."""
    if not value:
        return "Unknown"

    try:
        dt = datetime.fromisoformat(str(value))
        market_tz = ZoneInfo(config.MARKET_TIMEZONE)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=market_tz)
        else:
            dt = dt.astimezone(market_tz)

        return dt.strftime("%b %d, %Y at %I:%M %p ET").replace(" 0", " ")

    except Exception:
        return str(value)


def format_short_float(short_float: float | None) -> str:
    if short_float is None:
        return "Missing"

    return f"{round(short_float * 100, 1)}%"


def format_tags(candidate: dict) -> str:
    return ", ".join(candidate.get("tags", [])) or "None"


def get_candidate_status_label(
    candidate: dict,
    alert_type: str | None = None,
) -> str:
    """
    Display-facing candidate label.

    Important:
    - Alert-state logic may internally use Cooling Off as the weak/fallback state.
    - Display logic should not call every weak ticker "Cooling Off."
    - Cooling Off should only display when it is explicitly tagged or when the
      scheduled alert type is a real cooling-off alert.
    """
    tags = candidate.get("tags", [])

    if alert_type == "cooling_off":
        return "Cooling Off"

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

    return "No Active Signal"


def get_candidate_color(
    candidate: dict,
    alert_type: str | None = None,
) -> discord.Color:
    label = get_candidate_status_label(candidate, alert_type=alert_type)

    if label == "High Conviction":
        return discord.Color.green()

    if label == "Squeeze Watch":
        return discord.Color.gold()

    if label == "Heating Up":
        return discord.Color.orange()

    if label == "Cooling Off":
        return discord.Color.blue()

    return discord.Color.light_grey()


def parse_conviction_upgrade_reason(reason: str | None) -> tuple[str | None, str | None]:
    """
    Parse reasons like:
    conviction upgrade: Heating Up -> Squeeze Watch
    conviction upgrade: Heating Up → Squeeze Watch
    """
    if not reason:
        return None, None

    normalized = reason.strip()

    if not normalized.lower().startswith("conviction upgrade:"):
        return None, None

    upgrade_path = normalized.split(":", 1)[1].strip()

    if "→" in upgrade_path:
        old_label, new_label = upgrade_path.split("→", 1)
    elif "->" in upgrade_path:
        old_label, new_label = upgrade_path.split("->", 1)
    else:
        return None, None

    old_label = old_label.strip()
    new_label = new_label.strip()

    if not old_label or not new_label:
        return None, None

    return old_label, new_label


def build_candidate_alert_embed(payload: dict) -> discord.Embed:
    """Build the multi-candidate embed used by /squeeze top."""
    candidates = payload.get("candidates", [])[: config.MAX_ALERTS_PER_SCAN]
    generated_at = format_generated_at(payload.get("generated_at"))

    embed = discord.Embed(
        title="Top Squeeze Candidates",
        description=(
            f"Generated: `{generated_at}`\n"
            f"Scanned: `{payload.get('scanned_count', 0):,}` | "
            f"Candidates: `{payload.get('candidate_count', 0):,}` | "
            f"Showing: `{len(candidates):,}`"
        ),
        color=discord.Color.purple(),
    )

    if payload.get("scan_skipped"):
        embed.add_field(
            name="Scan Skipped",
            value=payload.get("skip_reason", "Unknown reason."),
            inline=False,
        )

    if not candidates:
        embed.add_field(
            name="No candidates found",
            value=(
                "No tickers currently passed the relative-volume and "
                "percent-move filters."
            ),
            inline=False,
        )
        return embed

    for index, candidate in enumerate(candidates, start=1):
        short_text = format_short_float(candidate.get("short_float"))
        tags = format_tags(candidate)
        label = get_candidate_status_label(candidate)

        embed.add_field(
            name=(
                f'{index}. {candidate["symbol"]} — '
                f'{label} — Score {candidate["score"]}'
            ),
            value=(
                f'Price: `${candidate["price"]}`\n'
                f'Move: `{candidate["percent_move"]}%`\n'
                f'Rel Vol: `{candidate["relative_volume"]}x`\n'
                f'Short Float: `{short_text}`\n'
                f'Volume: `{candidate["latest_volume"]:,}`\n'
                f'Tags: `{tags}`'
            ),
            inline=False,
        )

    return embed


def build_single_candidate_alert_embed(
    candidate: dict,
    payload: dict,
    rank: int,
    reason: str | None = None,
    alert_type: str | None = None,
) -> discord.Embed:
    """Build one scheduled-alert embed for a single alertable candidate."""
    symbol = candidate.get("symbol", "UNKNOWN")
    score = candidate.get("score", 0)
    label = get_candidate_status_label(candidate, alert_type=alert_type)
    tags = format_tags(candidate)
    short_text = format_short_float(candidate.get("short_float"))
    generated_at = format_generated_at(payload.get("generated_at"))

    old_label, new_label = parse_conviction_upgrade_reason(reason)

    if old_label and new_label:
        title = f"{symbol} — Upgrade: {old_label} → {new_label}"
    else:
        title = f"{symbol} — {label}"

    embed = discord.Embed(
        title=title,
        description=(
            f"Status: `{label}`\n"
            f"Rank: `#{rank}`\n"
            f"Score: `{score}`\n"
            f"Generated: `{generated_at}`"
        ),
        color=get_candidate_color(candidate, alert_type=alert_type),
    )

    if old_label and new_label and reason:
        embed.add_field(
            name="Alert Reason",
            value=f"`{reason}`",
            inline=False,
        )

    embed.add_field(
        name="Price / Move",
        value=(
            f'Price: `${candidate.get("price", "N/A")}`\n'
            f'Move: `{candidate.get("percent_move", "N/A")}%`'
        ),
        inline=True,
    )

    embed.add_field(
        name="Volume",
        value=(
            f'Rel Vol: `{candidate.get("relative_volume", "N/A")}x`\n'
            f'Latest: `{candidate.get("latest_volume", 0):,}`'
        ),
        inline=True,
    )

    embed.add_field(
        name="Short Interest",
        value=(
            f"Short Float: `{short_text}`\n"
            f'Source: `{candidate.get("short_interest_source", "unknown")}`'
        ),
        inline=True,
    )

    embed.add_field(
        name="Tags",
        value=f"`{tags}`",
        inline=False,
    )

    embed.set_footer(
        text=(
            f"Scanned {payload.get('scanned_count', 0):,} tickers | "
            f"{payload.get('candidate_count', 0):,} candidates found"
        )
    )

    return embed


def build_ticker_candidate_embed(
    candidate: dict,
    payload: dict,
    rank: int | None = None,
) -> discord.Embed:
    """Build one candidate-style embed for /squeeze ticker SYMBOL."""
    symbol = candidate.get("symbol", "UNKNOWN")
    score = candidate.get("score", 0)
    label = get_candidate_status_label(candidate)
    tags = format_tags(candidate)
    short_text = format_short_float(candidate.get("short_float"))
    generated_at = format_generated_at(payload.get("generated_at"))

    rank_text = f"Rank: `#{rank}`\n" if rank is not None else ""

    embed = discord.Embed(
        title=f"{symbol} — {label}",
        description=(
            f"{rank_text}"
            f"Score: `{score}`\n"
            f"Generated: `{generated_at}`"
        ),
        color=get_candidate_color(candidate),
    )

    embed.add_field(
        name="Price / Move",
        value=(
            f'Price: `${candidate.get("price", "N/A")}`\n'
            f'Move: `{candidate.get("percent_move", "N/A")}%`'
        ),
        inline=True,
    )

    embed.add_field(
        name="Volume",
        value=(
            f'Rel Vol: `{candidate.get("relative_volume", "N/A")}x`\n'
            f'Latest: `{candidate.get("latest_volume", 0):,}`'
        ),
        inline=True,
    )

    embed.add_field(
        name="Short Interest",
        value=(
            f"Short Float: `{short_text}`\n"
            f'Source: `{candidate.get("short_interest_source", "unknown")}`'
        ),
        inline=True,
    )

    embed.add_field(
        name="Tags",
        value=f"`{tags}`",
        inline=False,
    )

    embed.set_footer(
        text=(
            f"Scanned {payload.get('scanned_count', 0):,} tickers | "
            f"{payload.get('candidate_count', 0):,} candidates found"
        )
    )

    return embed


def build_alert_history_embed(rows: list[dict]) -> discord.Embed:
    """Build recent alert-history embed for /squeeze admin action:alerts."""
    embed = discord.Embed(
        title="Recent Squeeze Alerts",
        description="Most recent alert history from cache.",
        color=discord.Color.blue(),
    )

    if not rows:
        embed.add_field(
            name="No alert history found",
            value="No scheduled alerts have been recorded yet.",
            inline=False,
        )
        return embed

    for index, row in enumerate(rows, start=1):
        symbol = row.get("symbol", "UNKNOWN")
        label = row.get("last_label") or get_candidate_status_label(row)
        last_alert_at = format_generated_at(row.get("last_alert_at"))
        tags = format_tags(row)

        embed.add_field(
            name=f"{index}. {symbol} — {label}",
            value=(
                f'Score: `{row.get("score", "N/A")}`\n'
                f'Move: `{row.get("percent_move", "N/A")}%`\n'
                f'Rel Vol: `{row.get("relative_volume", "N/A")}x`\n'
                f'Reason: `{row.get("reason", "unknown")}`\n'
                f'Last Alert: `{last_alert_at}`\n'
                f'Tags: `{tags}`'
            ),
            inline=False,
        )

    return embed