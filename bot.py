import asyncio

import discord
from discord import app_commands
from discord.ext import tasks

import config
from src.logger import setup_logger
from src.discord_commands import SqueezeCommandGroup
from src.market_hours import is_market_open, market_status_text, now_et
from src.alert_state import (
    get_candidate_label,
    get_previous_candidate_alert,
    mark_missing_candidates_expired,
    record_candidate_alert,
    record_candidate_seen,
    should_post_cooling_off_alert,
    should_post_discovery_alert,
)
from src.discord_embeds import (
    build_candidate_alert_embed,
    build_single_candidate_alert_embed,
)


logger = setup_logger("bot", "bot.log")


class SqueezeBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)

        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if config.DISCORD_GUILD_ID:
            guild = discord.Object(id=config.DISCORD_GUILD_ID)

            logger.info("⚙️  Using guild-specific slash command sync.")

            self.tree.add_command(SqueezeCommandGroup(), guild=guild)

            synced = await self.tree.sync(guild=guild)

            logger.info(
                "🔑 Slash commands synced to guild %s: %s command(s)",
                config.DISCORD_GUILD_ID,
                len(synced),
            )

        else:
            self.tree.add_command(SqueezeCommandGroup())

            synced = await self.tree.sync()
            logger.info("Slash commands synced globally: %s command(s)", len(synced))

        if not self.scheduled_scan_loop.is_running():
            self.scheduled_scan_loop.start()
            logger.info("🔍 Scheduled scan loop started.")

    async def on_ready(self):
        logger.info("✅ Bot logged in as %s", self.user)

        channel = await self.get_alert_channel()

        if channel:
            try:
                await channel.send(
                    "Discord Squeeze Bot is online. Slash commands loaded. 📈"
                )
                logger.info(
                    "📟 Startup message sent to channel ID: %s",
                    config.DISCORD_CHANNEL_ID,
                )

            except discord.Forbidden:
                logger.error(
                    "❌ Bot can access channel but cannot send messages there: %s",
                    config.DISCORD_CHANNEL_ID,
                )

            except discord.HTTPException as exc:
                logger.error("❌ Failed to send startup message: %s", exc)

        else:
            logger.warning(
                "❌ Could not access Discord channel ID: %s",
                config.DISCORD_CHANNEL_ID,
            )

    async def get_alert_channel(self):
        channel = self.get_channel(config.DISCORD_CHANNEL_ID)

        if channel is not None:
            return channel

        try:
            channel = await self.fetch_channel(config.DISCORD_CHANNEL_ID)
            logger.info("💾 Fetched channel directly: %s", channel)
            return channel

        except discord.NotFound:
            logger.error(
                "❌ Channel ID not found: %s",
                config.DISCORD_CHANNEL_ID,
            )

        except discord.Forbidden:
            logger.error(
                "❌ Bot does not have permission to access channel ID: %s",
                config.DISCORD_CHANNEL_ID,
            )

        except discord.HTTPException as exc:
            logger.error("❌ Failed to fetch channel: %s", exc)

        return None

    def build_candidate_alert_embed(self, payload: dict) -> discord.Embed:
        return build_candidate_alert_embed(payload)

    def build_single_candidate_alert_embed(
        self,
        candidate: dict,
        payload: dict,
        rank: int,
        reason: str | None = None,
        alert_type: str | None = None,
    ) -> discord.Embed:
        return build_single_candidate_alert_embed(
            candidate,
            payload,
            rank,
            reason=reason,
            alert_type=alert_type,
        )

    @tasks.loop(minutes=config.SCAN_INTERVAL_MINUTES)
    async def scheduled_scan_loop(self):
        current_time = now_et()
        market_status = market_status_text(current_time)
        market_open = is_market_open(current_time)

        # DEV ONLY: lets us test scheduled scanning while the real market is closed.
        # Keep FORCE_MARKET_OPEN_FOR_TESTING = False in production.
        if getattr(config, "FORCE_MARKET_OPEN_FOR_TESTING", False):
            market_open = True
            market_status = "FORCED OPEN - DEV TEST"

        logger.info("📡 Scheduled scan tick reached. Market status: %s", market_status)

        if config.SCAN_MARKET_HOURS_ONLY and not market_open:
            logger.info("Market closed. Scheduled scan skipped.")
            return

        channel = await self.get_alert_channel()

        if channel is None:
            logger.error("Scheduled scan could not access alert channel.")
            return

        try:
            from src.squeeze_scanner import run_squeeze_scan

            payload = await asyncio.to_thread(
                run_squeeze_scan,
                limit=config.MAX_ALERTS_PER_SCAN,
                force=False,
            )

        except Exception as exc:
            logger.exception("❌ Scheduled squeeze scan failed: %s", exc)
            return

        if payload.get("scan_skipped"):
            logger.info(
                "Scheduled scan did not run. Reason: %s",
                payload.get("skip_reason", "unknown"),
            )
            return

        all_candidates = payload.get("all_candidates") or payload.get("candidates", [])

        if not all_candidates:
            expired_count = mark_missing_candidates_expired(set())
            logger.info(
                "Scheduled scan ran but found no candidates. No alert posted. "
                "Quiet expirations marked: %s",
                expired_count,
            )
            return

        sent_count = 0
        skipped_count = 0
        evaluated_count = 0
        eligible_alerts = []
        seen_symbols = set()

        scheduled_alert_limit = int(
            getattr(
                config,
                "SCHEDULED_ALERTS_PER_SCAN",
                config.MAX_ALERTS_PER_SCAN,
            )
            or 1
        )

        def get_alert_priority(label: str, alert_type: str, reason: str) -> int:
            if alert_type == "discovery" and reason.startswith("conviction upgrade"):
                return 0

            if label == "High Conviction":
                return 1

            if label == "Squeeze Watch":
                return 2

            if label == "Heating Up":
                return 3

            if alert_type == "cooling_off":
                return 4

            return 9

        for rank, raw_candidate in enumerate(all_candidates, start=1):
            evaluated_count += 1

            candidate = dict(raw_candidate)
            candidate["rank"] = rank

            symbol = candidate.get("symbol", "UNKNOWN")
            previous = get_previous_candidate_alert(symbol)

            if symbol and symbol != "UNKNOWN":
                seen_symbols.add(symbol.upper().strip())

            should_alert, discovery_reason = should_post_discovery_alert(
                candidate,
                previous,
            )
            reason = discovery_reason
            alert_type = "discovery"

            if not should_alert:
                should_cooling_alert, cooling_reason = should_post_cooling_off_alert(
                    candidate,
                    previous,
                )

                if should_cooling_alert:
                    should_alert = True
                    reason = cooling_reason
                    alert_type = "cooling_off"
                else:
                    reason = f"{discovery_reason} | {cooling_reason}"

            # Save per-scan setup state after event classification, but keep using
            # the pre-update previous row above for alert decisions.
            record_candidate_seen(candidate, rank, previous)

            if not should_alert:
                skipped_count += 1
                logger.info(
                    "Scheduled alert skipped for %s: %s",
                    symbol,
                    reason,
                )
                continue

            label = get_candidate_label(candidate)

            eligible_alerts.append(
                {
                    "candidate": candidate,
                    "rank": rank,
                    "symbol": symbol,
                    "reason": reason,
                    "alert_type": alert_type,
                    "label": label,
                    "priority": get_alert_priority(label, alert_type, reason),
                }
            )

        expired_count = mark_missing_candidates_expired(seen_symbols)

        if not eligible_alerts:
            logger.info(
                "Scheduled scan found no eligible alert state changes. "
                "Evaluated: %s | Candidates: %s | Quiet expirations marked: %s",
                evaluated_count,
                len(all_candidates),
                expired_count,
            )
            return

        eligible_alerts.sort(
            key=lambda item: (
                item["priority"],
                item["rank"],
            )
        )

        alerts_to_send = eligible_alerts[:scheduled_alert_limit]

        logger.info(
            "Scheduled alert priority pass. Eligible: %s | Sending: %s | Limit: %s",
            len(eligible_alerts),
            len(alerts_to_send),
            scheduled_alert_limit,
        )

        for index, alert in enumerate(alerts_to_send):
            if index > 0:
                spacing_seconds = int(
                    getattr(config, "ALERT_POST_SPACING_SECONDS", 0) or 0
                )

                if spacing_seconds > 0:
                    logger.info(
                        "Waiting %s seconds before next scheduled alert post.",
                        spacing_seconds,
                    )
                    await asyncio.sleep(spacing_seconds)

            candidate = alert["candidate"]
            rank = alert["rank"]
            symbol = alert["symbol"]
            reason = alert["reason"]
            alert_type = alert["alert_type"]

            embed = self.build_single_candidate_alert_embed(
                candidate,
                payload,
                rank,
                reason=reason,
                alert_type=alert_type,
            )

            try:
                await channel.send(embed=embed)

                record_candidate_alert(
                    candidate=candidate,
                    rank=rank,
                    reason=reason,
                    alert_type=alert_type,
                )

                sent_count += 1

                logger.info(
                    "Posted scheduled squeeze alert for %s. Type: %s | Reason: %s",
                    symbol,
                    alert_type,
                    reason,
                )

            except discord.Forbidden:
                logger.error(
                    "Bot cannot send scheduled scan alert to channel ID: %s",
                    config.DISCORD_CHANNEL_ID,
                )
                return

            except discord.HTTPException as exc:
                logger.error(
                    "Failed to send scheduled scan alert for %s: %s",
                    symbol,
                    exc,
                )

        logger.info(
            "Scheduled scan alert pass complete. "
            "Sent: %s | Skipped: %s | Eligible: %s | Evaluated: %s | "
            "All Candidates: %s | Quiet Expirations: %s",
            sent_count,
            skipped_count,
            len(eligible_alerts),
            evaluated_count,
            len(all_candidates),
            expired_count,
        )

    @scheduled_scan_loop.before_loop
    async def before_scheduled_scan_loop(self):
        await self.wait_until_ready()


bot = SqueezeBot()


async def main():
    if not config.DISCORD_TOKEN:
        raise RuntimeError("Missing DISCORD_TOKEN in .env")

    if not config.DISCORD_CHANNEL_ID:
        raise RuntimeError("Missing DISCORD_CHANNEL_ID in .env")

    if not config.DISCORD_GUILD_ID:
        logger.warning(
            "DISCORD_GUILD_ID is missing. Commands will sync globally and may take longer to appear."
        )

    try:
        await bot.start(config.DISCORD_TOKEN)

    except asyncio.CancelledError:
        logger.info("Bot shutdown task cancelled.")
        raise

    finally:
        if bot.scheduled_scan_loop.is_running():
            bot.scheduled_scan_loop.cancel()
            logger.info("Scheduled scan loop cancelled.")

        if not bot.is_closed():
            await bot.close()
            logger.info("Discord client closed cleanly.")


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Bot stopped manually.")