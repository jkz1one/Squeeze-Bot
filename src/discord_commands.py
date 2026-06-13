import asyncio

import discord
from discord import app_commands

import config
from src.market_hours import is_market_open, market_status_text, now_et
from src.enriched_universe_manager import get_enriched_universe_summary

from src.squeeze_scanner import (
    build_single_ticker_report,
    get_top_candidates,
    load_latest_candidates,
    refresh_missing_short_interest_for_latest_candidates,
    run_squeeze_scan,
)
from src.discord_embeds import (
    add_reason_fields_to_embed,
    build_alert_history_embed,
    build_candidate_alert_embed,
    build_ticker_candidate_embed,
    format_generated_at,
)
from src.alert_state import (
    clear_all_alert_history,
    clear_ticker_alert_state,
    get_daily_setup_recap,
    get_recent_alert_history,
    get_ticker_alert_state,
)


# Change this one line to control the color of all admin-control embeds.
ADMIN_EMBED_COLOR = discord.Color.from_rgb(10, 10, 10)

# Alternative dark/black-style option:
# ADMIN_EMBED_COLOR = discord.Color.from_rgb(10, 10, 10)


def is_admin_user(interaction: discord.Interaction) -> bool:
    return interaction.user.id in config.DISCORD_ADMIN_IDS


async def reject_non_admin(interaction: discord.Interaction) -> bool:
    if is_admin_user(interaction):
        return False

    await interaction.response.send_message(
        "You do not have permission to run this command.",
        ephemeral=True,
    )
    return True


def build_universe_embed(summary: dict, refresh: bool = False) -> discord.Embed:
    embed = discord.Embed(
        title="Squeeze Universe Refreshed" if refresh else "Squeeze Universe",
        description=(
            "Universe cache was rebuilt from source data."
            if refresh
            else "Current enriched and filtered ticker universe."
        ),
        color=ADMIN_EMBED_COLOR,
    )

    embed.add_field(
        name="Base Count",
        value=f'{summary["base_count"]:,}',
        inline=True,
    )

    embed.add_field(
        name="Enriched",
        value=f'{summary["enriched_count"]:,}',
        inline=True,
    )

    embed.add_field(
        name="Passed Filters",
        value=f'{summary["passed_count"]:,}',
        inline=True,
    )

    embed.add_field(
        name="Source",
        value="YFinance Enriched",
        inline=True,
    )

    embed.add_field(
        name="Base Source",
        value="Nasdaq Trader",
        inline=True,
    )

    embed.add_field(
        name="Age",
        value=f'{summary["age_days"]} days',
        inline=True,
    )

    embed.add_field(
        name="Generated At",
        value=summary["generated_at"],
        inline=False,
    )

    embed.add_field(
        name="Min Price",
        value=f'${summary["min_price"]}',
        inline=True,
    )

    embed.add_field(
        name="Min Avg Volume",
        value=f'{summary["min_avg_volume"]:,}',
        inline=True,
    )

    return embed


def build_admin_scan_embed(payload: dict, market_status: str) -> discord.Embed:
    candidates = payload.get("candidates", [])

    embed = discord.Embed(
        title="Admin Scan Complete",
        description=(
            f"Market status: `{market_status}`\n"
            f"Universe: "
            f"`{payload.get('scan_universe_count', payload.get('universe_count', 0)):,}`\n"
            f"Scanned: `{payload.get('scanned_count', 0):,}`\n"
            f"Candidates Found: `{payload.get('candidate_count', 0):,}`\n"
            f"Short Interest Connected: "
            f"`{payload.get('short_interest_connected_count', 0):,}`\n"
            f"Duration: `{payload.get('scan_duration_seconds', 0)}s`\n"
            f"Chunks: `{payload.get('chunks_fetched', 0)}/"
            f"{payload.get('chunks_total', 0)}` "
            f"failed `{payload.get('chunks_failed', 0)}`"
        ),
        color=ADMIN_EMBED_COLOR,
    )

    if payload.get("scan_skipped"):
        embed.add_field(
            name="Scan Skipped",
            value=payload.get("skip_reason", "Unknown reason."),
            inline=False,
        )
        return embed

    if not candidates:
        embed.add_field(
            name="No candidates passed",
            value=(
                "No tickers passed the current squeeze thresholds.\n"
                f"Rel Vol Threshold: `{config.SQUEEZE_REL_VOL_THRESH}x`\n"
                f"Move Threshold: `{config.SQUEEZE_PCT_MOVE_THRESH}%`"
            ),
            inline=False,
        )
        return embed

    for index, candidate in enumerate(candidates, start=1):
        tags = candidate.get("tags", [])
        status = "Candidate"

        for possible_status in [
            "High Conviction",
            "Squeeze Watch",
            "Heating Up",
            "Cooling Off",
        ]:
            if possible_status in tags:
                status = possible_status
                break

        tags_text = ", ".join(candidate.get("tags", [])) or "None"
        short_float = candidate.get("short_float")

        if short_float is None:
            short_text = "Missing"
        else:
            short_text = f"{round(short_float * 100, 1)}%"

        embed.add_field(
            name=(
                f'{index}. {candidate["symbol"]} — '
                f'{status} — Score {candidate["score"]}'
            ),
            value=(
                f'Price: `${candidate["price"]}`\n'
                f'Move: `{candidate["percent_move"]}%`\n'
                f'Rel Vol: `{candidate["relative_volume"]}x`\n'
                f'Short Float: `{short_text}`\n'
                f'Volume: `{candidate["latest_volume"]:,}`\n'
                f'Tags: `{tags_text}`'
            ),
            inline=False,
        )

    return embed


def build_refresh_shorts_embed(summary: dict) -> discord.Embed:
    embed = discord.Embed(
        title="Short Interest Recovery",
        description=summary.get("reason", "Short-interest refresh finished."),
        color=ADMIN_EMBED_COLOR,
    )

    embed.add_field(
        name="Missing Before",
        value=f'`{summary.get("missing_before", 0):,}`',
        inline=True,
    )

    embed.add_field(
        name="Attempted",
        value=f'`{summary.get("attempted", 0):,}`',
        inline=True,
    )

    embed.add_field(
        name="Recovered",
        value=f'`{summary.get("recovered", 0):,}`',
        inline=True,
    )

    embed.add_field(
        name="Still Missing",
        value=f'`{summary.get("still_missing", 0):,}`',
        inline=True,
    )

    embed.add_field(
        name="Candidates Re-scored",
        value=f'`{summary.get("candidates_rescored", 0):,}`',
        inline=True,
    )

    embed.add_field(
        name="Top Cache Updated",
        value=f'`{summary.get("top_cache_updated", False)}`',
        inline=True,
    )

    if summary.get("short_interest_connected_count") is not None:
        embed.add_field(
            name="Short Interest Connected",
            value=f'`{summary.get("short_interest_connected_count", 0):,}`',
            inline=True,
        )

    recovered_symbols = summary.get("recovered_symbols", [])

    if recovered_symbols:
        preview = ", ".join(recovered_symbols[:20])

        if len(recovered_symbols) > 20:
            preview += f", +{len(recovered_symbols) - 20} more"

        embed.add_field(
            name="Recovered Symbols",
            value=f"`{preview}`",
            inline=False,
        )

    still_missing_symbols = summary.get("still_missing_symbols", [])

    if still_missing_symbols:
        preview = ", ".join(still_missing_symbols[:20])

        if len(still_missing_symbols) > 20:
            preview += f", +{len(still_missing_symbols) - 20} more"

        embed.add_field(
            name="Still Missing Symbols",
            value=f"`{preview}`",
            inline=False,
        )

    return embed


def _format_state_value(value) -> str:
    if value is None or value == "":
        return "N/A"

    return str(value)


def _format_score_change(previous_score, current_score) -> str:
    previous_text = _format_state_value(previous_score)
    current_text = _format_state_value(current_score)

    if previous_score is None:
        return current_text

    return f"{previous_text} → {current_text}"


def _format_class_change(previous_classification, current_classification) -> str:
    previous_text = _format_state_value(previous_classification)
    current_text = _format_state_value(current_classification)

    if previous_classification is None:
        return current_text

    return f"{previous_text} → {current_text}"


def build_ticker_alert_state_embed(
    symbol: str,
    row: dict | None,
    color: discord.Color = ADMIN_EMBED_COLOR,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Alert State — {symbol}",
        color=color,
    )

    if not row:
        embed.description = "No saved alert state found for this ticker."
        return embed

    current_event_type = row.get("current_event_type") or row.get("last_event_type")
    current_score = row.get("current_score", row.get("score"))
    previous_score = row.get("previous_score")
    current_classification = row.get("current_classification") or row.get("last_label")
    previous_classification = row.get("previous_classification")

    embed.description = (
        f"Event: `{_format_state_value(current_event_type)}`\n"
        f"Status: `{_format_state_value(current_classification)}`\n"
        f"Expired: `{bool(row.get('expired', False))}` | "
        f"Cooling Off: `{bool(row.get('cooling_off', False))}` | "
        f"Active Today: `{bool(row.get('active_today', False))}`"
    )

    embed.add_field(
        name="Score State",
        value=(
            f"Score: `{_format_score_change(previous_score, current_score)}`\n"
            f"Change: `{_format_state_value(row.get('score_change'))}`\n"
            f"Peak Today: `{_format_state_value(row.get('peak_score_today'))}`"
        ),
        inline=True,
    )

    embed.add_field(
        name="Classification",
        value=(
            f"Class: `{_format_class_change(previous_classification, current_classification)}`\n"
            f"Peak Class: `{_format_state_value(row.get('peak_classification_today'))}`\n"
            f"Last Alert Type: `{_format_state_value(row.get('last_alert_type'))}`"
        ),
        inline=True,
    )

    embed.add_field(
        name="Rank / Alerts",
        value=(
            f"Rank: `#{_format_state_value(row.get('rank'))}`\n"
            f"Alert Count Today: `{_format_state_value(row.get('alert_count_today', 0))}`\n"
            f"Missed Scans: `{_format_state_value(row.get('missed_scan_count', 0))}`"
        ),
        inline=True,
    )

    embed.add_field(
        name="Signal",
        value=(
            f"Move: `{_format_state_value(row.get('previous_percent_move'))} → "
            f"{_format_state_value(row.get('percent_move'))}%`\n"
            f"Rel Vol: `{_format_state_value(row.get('previous_relative_volume'))} → "
            f"{_format_state_value(row.get('relative_volume'))}x`\n"
            f"Short Float: `{_format_state_value(row.get('short_float'))}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Timing",
        value=(
            f"First Seen: `{format_generated_at(row.get('first_seen_at'))}`\n"
            f"Last Seen: `{format_generated_at(row.get('last_seen_at'))}`\n"
            f"Last Alert: `{format_generated_at(row.get('last_alert_at'))}`\n"
            f"Expired At: `{format_generated_at(row.get('expired_at'))}`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Reason",
        value=f"`{row.get('reason') or row.get('last_alert_reason') or 'No posted alert reason yet.'}`",
        inline=False,
    )

    tags = ", ".join(row.get("tags", [])) or "None"

    embed.add_field(
        name="Tags",
        value=f"`{tags}`",
        inline=False,
    )

    add_reason_fields_to_embed(embed, row, state=row, include_components=True)

    return embed


def _format_recap_rows(rows: list[dict], empty: str = "None") -> str:
    if not rows:
        return empty

    lines = []

    for row in rows[:5]:
        symbol = row.get("symbol", "UNKNOWN")
        label = row.get("current_classification") or row.get("last_label") or "N/A"
        score = row.get("current_score", row.get("score", "N/A"))
        change = row.get("score_change")

        if change is None:
            change_text = ""
        else:
            try:
                change_number = float(change)
                sign = "+" if change_number > 0 else ""
                change_text = f" ({sign}{round(change_number, 2)})"
            except (TypeError, ValueError):
                change_text = f" ({change})"

        lines.append(f"• `{symbol}` — {label} — Score `{score}`{change_text}")

    return "\n".join(lines)


def build_daily_recap_embed(summary: dict) -> discord.Embed:
    embed = discord.Embed(
        title="Squeeze Setup Recap",
        description=(
            f"Trading Date: `{summary.get('trading_date', 'N/A')}`\n"
            f"Tracked: `{summary.get('total_tracked', 0)}` | "
            f"Active: `{summary.get('active_count', 0)}` | "
            f"Cooling: `{summary.get('cooling_count', 0)}` | "
            f"Expired: `{summary.get('expired_count', 0)}` | "
            f"Alerted: `{summary.get('alerted_count', 0)}`"
        ),
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="Event Counts",
        value=(
            f"New: `{summary.get('new_discovery_count', 0)}`\n"
            f"Upgrades: `{summary.get('upgrade_count', 0)}`\n"
            f"Score Surges: `{summary.get('score_surge_count', 0)}`"
        ),
        inline=True,
    )

    embed.add_field(
        name="Top Active",
        value=_format_recap_rows(summary.get("top_active", [])),
        inline=False,
    )

    embed.add_field(
        name="Biggest Improvers",
        value=_format_recap_rows(summary.get("biggest_improvers", [])),
        inline=False,
    )

    embed.add_field(
        name="Cooling Off",
        value=_format_recap_rows(summary.get("cooling_off", [])),
        inline=False,
    )

    embed.add_field(
        name="Quietly Expired",
        value=_format_recap_rows(summary.get("expired", [])),
        inline=False,
    )

    return embed


def build_explain_embed(
    symbol: str,
    candidate: dict,
    payload: dict,
    state: dict | None = None,
    rank: int | None = None,
) -> discord.Embed:
    label = (state or {}).get("current_classification") or "Live Check"

    embed = discord.Embed(
        title=f"Explain — {symbol}",
        description=(
            f"Status: `{label}`\n"
            f"Score: `{candidate.get('score', 'N/A')}`\n"
            f"Rank: `{f'#{rank}' if rank is not None else 'N/A'}`\n"
            f"Checked: `{format_generated_at(payload.get('generated_at'))}`"
        ),
        color=discord.Color.blue(),
    )

    embed.add_field(
        name="Live Data",
        value=(
            f"Price: `${candidate.get('price', 'N/A')}`\n"
            f"Move: `{candidate.get('percent_move', 'N/A')}%`\n"
            f"Rel Vol: `{candidate.get('relative_volume', 'N/A')}x`\n"
            f"Short Float: `{candidate.get('short_float', 'Missing')}`"
        ),
        inline=False,
    )

    if state:
        embed.add_field(
            name="Saved State",
            value=(
                f"Event: `{state.get('current_event_type', 'N/A')}`\n"
                f"Score Change: `{state.get('score_change', 'N/A')}`\n"
                f"Peak Score Today: `{state.get('peak_score_today', 'N/A')}`\n"
                f"Last Seen: `{format_generated_at(state.get('last_seen_at'))}`"
            ),
            inline=False,
        )

    add_reason_fields_to_embed(embed, candidate, state=state, include_components=True)

    return embed


class SqueezeCommandGroup(app_commands.Group):
    def __init__(self):
        super().__init__(
            name="squeeze",
            description="Short squeeze alert bot commands.",
        )

    @app_commands.command(name="status", description="Show squeeze bot status.")
    async def status(self, interaction: discord.Interaction):
        current_time = now_et()
        market_open = is_market_open(current_time)
        market_status = market_status_text(current_time)

        embed = discord.Embed(
            title="Discord Squeeze Bot Status",
            description="Squeeze bot command flow is online.",
            color=discord.Color.green() if market_open else discord.Color.orange(),
        )

        embed.add_field(name="Bot", value="Online", inline=True)

        embed.add_field(
            name="Market",
            value=market_status,
            inline=True,
        )

        embed.add_field(
            name="Time",
            value=current_time.strftime("%Y-%m-%d %H:%M:%S"),
            inline=True,
        )

        embed.add_field(
            name="Scan Interval",
            value=f"{config.SCAN_INTERVAL_MINUTES} min",
            inline=True,
        )

        embed.add_field(
            name="Max Alerts / Scan",
            value=str(config.MAX_ALERTS_PER_SCAN),
            inline=True,
        )

        embed.add_field(
            name="Market Hours Only",
            value=str(config.SCAN_MARKET_HOURS_ONLY),
            inline=True,
        )

        embed.add_field(
            name="Universe Max Age",
            value=f"{config.UNIVERSE_MAX_AGE_DAYS} days",
            inline=True,
        )

        embed.add_field(
            name="Cooldown Mode",
            value=config.ALERT_COOLDOWN_MODE,
            inline=True,
        )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="admin",
        description="Admin controls for scans, alerts, and universe cache.",
    )
    @app_commands.describe(
        action="Admin action to run.",
        symbol="Optional ticker symbol for refresh-shorts, alert-state, or clear-alerts.",
        refresh="For universe action: force rebuild before showing status.",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="scan", value="scan"),
            app_commands.Choice(name="refresh-shorts", value="refresh-shorts"),
            app_commands.Choice(name="universe", value="universe"),
            app_commands.Choice(name="alert-state", value="alert-state"),
            app_commands.Choice(name="clear-alerts", value="clear-alerts"),
        ]
    )
    async def admin(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        symbol: str | None = None,
        refresh: bool = False,
    ):
        if await reject_non_admin(interaction):
            return

        await interaction.response.defer()

        selected_action = action.value
        clean_symbol = symbol.upper().strip() if symbol else None

        if selected_action == "scan":
            current_time = now_et()
            market_status = market_status_text(current_time)

            payload = await asyncio.to_thread(
                run_squeeze_scan,
                config.MAX_ALERTS_PER_SCAN,
                True,
            )

            embed = build_admin_scan_embed(payload, market_status)
            await interaction.followup.send(embed=embed)
            return

        if selected_action == "refresh-shorts":
            summary = await asyncio.to_thread(
                refresh_missing_short_interest_for_latest_candidates,
                config.MAX_ALERTS_PER_SCAN,
                0.15,
                clean_symbol,
            )

            embed = build_refresh_shorts_embed(summary)
            await interaction.followup.send(embed=embed)
            return

        if selected_action == "universe":
            summary = await asyncio.to_thread(
                get_enriched_universe_summary,
                refresh,
            )

            embed = build_universe_embed(summary, refresh=refresh)
            await interaction.followup.send(embed=embed)
            return

        if selected_action == "alert-state":
            if not clean_symbol:
                embed = discord.Embed(
                    title="Missing Symbol",
                    description="Use `/squeeze admin action:alert-state symbol:<TICKER>`.",
                    color=ADMIN_EMBED_COLOR,
                )
                await interaction.followup.send(embed=embed)
                return

            row = get_ticker_alert_state(clean_symbol)
            embed = build_ticker_alert_state_embed(clean_symbol, row)
            await interaction.followup.send(embed=embed)
            return

        if selected_action == "clear-alerts":
            if clean_symbol:
                removed = clear_ticker_alert_state(clean_symbol)

                embed = discord.Embed(
                    title="Alert State Cleared",
                    description=(
                        f"Cleared alert state for `{clean_symbol}`."
                        if removed
                        else f"No alert state found for `{clean_symbol}`."
                    ),
                    color=ADMIN_EMBED_COLOR,
                )

                await interaction.followup.send(embed=embed)
                return

            clear_all_alert_history()

            embed = discord.Embed(
                title="Alert History Cleared",
                description="All saved alert state has been cleared.",
                color=ADMIN_EMBED_COLOR,
            )

            await interaction.followup.send(embed=embed)
            return

        embed = discord.Embed(
            title="Unknown Admin Action",
            description=f"`{selected_action}` is not supported.",
            color=ADMIN_EMBED_COLOR,
        )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="alerts",
        description="Show recent squeeze alert history.",
    )
    @app_commands.describe(
        symbol="Optional ticker symbol.",
    )
    async def alerts(
        self,
        interaction: discord.Interaction,
        symbol: str | None = None,
    ):
        await interaction.response.defer()

        clean_symbol = symbol.upper().strip() if symbol else None

        if clean_symbol:
            row = get_ticker_alert_state(clean_symbol)
            embed = build_ticker_alert_state_embed(
                clean_symbol,
                row,
                color=discord.Color.blue(),
            )
            await interaction.followup.send(embed=embed)
            return

        rows = get_recent_alert_history(limit=10)
        embed = build_alert_history_embed(rows)

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="recap",
        description="Show today's setup-monitor recap.",
    )
    async def recap(self, interaction: discord.Interaction):
        await interaction.response.defer()

        summary = get_daily_setup_recap(limit=5)
        embed = build_daily_recap_embed(summary)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="top", description="Show cached top squeeze candidates.")
    async def top(self, interaction: discord.Interaction):
        await interaction.response.defer()

        payload = get_top_candidates(force_scan=False, limit=config.MAX_ALERTS_PER_SCAN)
        embed = build_candidate_alert_embed(payload)

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="ticker",
        description="Show squeeze data for one ticker.",
    )
    @app_commands.describe(symbol="Ticker symbol, like GME or AMC")
    async def ticker(self, interaction: discord.Interaction, symbol: str):
        await interaction.response.defer()

        clean_symbol = symbol.upper().strip()
        cached_rank = None

        cached_payload = load_latest_candidates()

        if cached_payload is not None:
            cached_candidates = (
                cached_payload.get("all_candidates")
                or cached_payload.get("candidates", [])
            )

            for index, cached_candidate in enumerate(cached_candidates, start=1):
                if cached_candidate.get("symbol", "").upper() == clean_symbol:
                    cached_rank = index
                    break

        single_payload = await asyncio.to_thread(
            build_single_ticker_report,
            clean_symbol,
        )

        if not single_payload.get("found"):
            embed = discord.Embed(
                title=f"{clean_symbol} — No Data",
                description=single_payload.get(
                    "error",
                    "No market data was returned for this ticker.",
                ),
                color=discord.Color.orange(),
            )

            await interaction.followup.send(embed=embed)
            return

        candidate = single_payload["candidate"]

        embed = build_ticker_candidate_embed(
            candidate=candidate,
            payload=single_payload,
            rank=cached_rank,
        )

        if not single_payload.get("passed_filters"):
            failed_reasons = single_payload.get("failed_reasons", [])

            readable_reasons = []

            for reason in failed_reasons:
                lower_reason = reason.lower()

                if "relative volume" in lower_reason:
                    readable_reasons.append("Volume is not elevated enough.")
                elif "percent move" in lower_reason:
                    readable_reasons.append("Price action is not confirming momentum.")
                elif "average volume" in lower_reason:
                    readable_reasons.append("Average trading volume is too low.")
                elif "price below" in lower_reason:
                    readable_reasons.append("Price is below the minimum filter.")
                else:
                    readable_reasons.append("One or more squeeze filters did not pass.")

            readable_reasons = list(dict.fromkeys(readable_reasons))

            embed.title = f"{clean_symbol} — No Active Signal"
            embed.description = (
                f"Score: `{candidate.get('score', 'N/A')}`\n"
                f"Checked live: `{format_generated_at(single_payload.get('generated_at'))}`"
            )

            embed.clear_fields()

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
                    f'Short Float: `{round(candidate.get("short_float") * 100, 1)}%`\n'
                    if candidate.get("short_float") is not None
                    else "Short Float: `Missing`\n"
                )
                + f'Source: `{candidate.get("short_interest_source", "unknown")}`',
                inline=True,
            )

            if readable_reasons:
                embed.add_field(
                    name="Signal Notes",
                    value="\n".join(f"• {reason}" for reason in readable_reasons),
                    inline=False,
                )

            hidden_no_signal_tags = {
                "Cooling Off",
                "Heating Up",
                "Squeeze Watch",
                "High Conviction",
            }

            display_tags = [
                tag
                for tag in candidate.get("tags", [])
                if tag not in hidden_no_signal_tags
            ]

            tags = ", ".join(display_tags) or "None"

            embed.add_field(
                name="Tags",
                value=f"`{tags}`",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="explain",
        description="Explain ticker using live data plus saved state.",
    )
    @app_commands.describe(symbol="Ticker symbol, like GME or AMC")
    async def explain(self, interaction: discord.Interaction, symbol: str):
        await interaction.response.defer()

        clean_symbol = symbol.upper().strip()
        cached_rank = None
        cached_payload = load_latest_candidates()

        if cached_payload is not None:
            cached_candidates = (
                cached_payload.get("all_candidates")
                or cached_payload.get("candidates", [])
            )

            for index, cached_candidate in enumerate(cached_candidates, start=1):
                if cached_candidate.get("symbol", "").upper() == clean_symbol:
                    cached_rank = index
                    break

        single_payload = await asyncio.to_thread(
            build_single_ticker_report,
            clean_symbol,
        )

        if not single_payload.get("found"):
            embed = discord.Embed(
                title=f"Explain — {clean_symbol}",
                description=single_payload.get(
                    "error",
                    "No market data was returned for this ticker.",
                ),
                color=discord.Color.orange(),
            )
            await interaction.followup.send(embed=embed)
            return

        candidate = single_payload["candidate"]
        state = get_ticker_alert_state(clean_symbol)

        embed = build_explain_embed(
            symbol=clean_symbol,
            candidate=candidate,
            payload=single_payload,
            state=state,
            rank=cached_rank,
        )

        if not single_payload.get("passed_filters"):
            failed_reasons = single_payload.get("failed_reasons", [])
            if failed_reasons:
                embed.add_field(
                    name="Filter Notes",
                    value="\n".join(f"• {reason}" for reason in failed_reasons[:5]),
                    inline=False,
                )

        await interaction.followup.send(embed=embed)
