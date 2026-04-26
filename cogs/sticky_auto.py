from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence
from typing import Any

import discord
from discord.ext import commands

from utils.storage import Storage
from utils.tick import TickMeter


_CUSTOM_EMOJI_RE = re.compile(r"^<a?:[a-zA-Z0-9_]+:(\d+)>$")
logger = logging.getLogger(__name__)
_COLOR_MAP = {
    "blue": discord.Color.blue().value,
    "blurple": discord.Color.blurple().value,
    "green": discord.Color.green().value,
    "red": discord.Color.red().value,
    "orange": discord.Color.orange().value,
    "yellow": discord.Color.yellow().value,
    "purple": discord.Color.purple().value,
    "magenta": discord.Color.magenta().value,
    "teal": discord.Color.teal().value,
}


class StickyAutoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.storage = Storage()
        self.tick_meter = getattr(bot, "tick_meter", TickMeter(self.storage))
        self._sticky_message_ids: dict[tuple[int, int], int] = {}
        self._sticky_message_signatures: dict[tuple[int, int], list[str]] = {}
        self._sticky_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        await self.storage.init_schema()

    def cog_unload(self) -> None:
        for task in self._sticky_tasks.values():
            task.cancel()
        self._sticky_tasks.clear()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._ensure_bind_ready()
        if message.guild is None:
            return
        if await self._is_self_sticky_message(message):
            return

        guild_id = message.guild.id

        sticky_enabled = await self.storage.is_management_module_enabled(guild_id, "sticky-message")
        if sticky_enabled:
            can_start = await self.tick_meter.start_work(guild_id, "sticky.message.match", stoppable=True)
            if can_start:
                await self._handle_sticky_message(message)

        auto_reaction_enabled = await self.storage.is_management_module_enabled(guild_id, "auto-reaction")
        if auto_reaction_enabled:
            can_start = await self.tick_meter.start_work(guild_id, "auto_reaction.match", stoppable=True)
            if can_start:
                await self._handle_auto_reaction(message)

    async def _handle_sticky_message(self, message: discord.Message) -> None:
        config = await self._load_running_section(message.guild.id, "sticky-message")
        if not isinstance(config, dict):
            return
        items = self._sticky_items_from_config(config)
        if not items:
            return
        chosen: tuple[dict[str, Any], dict[str, Any]] | None = None
        for item in sorted((row for row in items if isinstance(row, dict)), key=lambda row: _safe_int(row.get("id"), 0)):
            trigger_bot_message = bool(item.get("trigger_bot_message", False))
            if message.author.bot and not trigger_bot_message:
                continue
            channel_entry = self._find_sticky_channel(item.get("channels", []), message.channel.id)
            if channel_entry is None:
                continue
            chosen = (item, channel_entry)
            break
        if chosen is None:
            return

        item, channel_entry = chosen
        delay = int(item.get("delay", 0) or 0)
        key = (message.guild.id, message.channel.id)
        existing = self._sticky_tasks.get(key)
        if existing is not None and not existing.done():
            existing.cancel()

        if delay <= 0:
            await self._apply_sticky_message(message.guild, message.channel, item, channel_entry)
            return

        async def run() -> None:
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            await self._apply_sticky_message(message.guild, message.channel, item, channel_entry)

        self._sticky_tasks[key] = asyncio.create_task(run())

    async def _apply_sticky_message(
        self,
        guild: discord.Guild,
        channel: discord.abc.MessageableChannel,
        config: dict[str, Any],
        channel_entry: dict[str, Any],
    ) -> None:
        key = (guild.id, channel.id)
        content = str(config.get("message", "") or "")
        embed = self._build_sticky_embed(config)
        if content == "" and embed is None:
            return

        await self.tick_meter.consume(guild.id, "sticky.message.apply", amount=1, stoppable=False)

        previous_id = self._sticky_message_ids.get(key)
        if previous_id is None:
            runtime = await self.storage.get_sticky_runtime(guild.id, channel.id)
            if runtime is not None:
                previous_id = runtime.message_id
                self._sticky_message_ids[key] = runtime.message_id
                if runtime.signature:
                    self._record_sticky_signature(guild.id, channel.id, runtime.signature)
        if previous_id is not None:
            if not hasattr(channel, "fetch_message"):
                await self.storage.insert_system_log_safe(
                    actor_user_id=None,
                    scope_id=guild.id,
                    feature="sticky-message",
                    severity="warn",
                    message="sticky-delete-failed",
                    detail_json={
                        "guild_id": int(guild.id),
                        "channel_id": int(channel.id),
                        "previous_message_id": int(previous_id),
                        "error_type": "fetch-unsupported",
                    },
                )
                return
            try:
                previous = await channel.fetch_message(previous_id)
                await previous.delete()
            except discord.NotFound:
                self._sticky_message_ids.pop(key, None)
                await self.storage.delete_sticky_runtime(guild.id, channel.id)
            except (discord.Forbidden, discord.HTTPException) as exc:
                await self.storage.insert_system_log_safe(
                    actor_user_id=None,
                    scope_id=guild.id,
                    feature="sticky-message",
                    severity="warn",
                    message="sticky-delete-failed",
                    detail_json={
                        "guild_id": int(guild.id),
                        "channel_id": int(channel.id),
                        "previous_message_id": int(previous_id),
                        "error_type": exc.__class__.__name__,
                    },
                )
                return

        sent_message: discord.Message | None = None
        send_mode = str(channel_entry.get("send_mode", "bot"))
        if send_mode == "webhook":
            sent_message = await self._send_sticky_via_webhook(guild.id, channel.id, channel_entry, content, embed)
        else:
            if hasattr(channel, "send"):
                try:
                    sent_message = await channel.send(content=content or None, embed=embed)
                except discord.HTTPException:
                    return
        if sent_message is not None:
            self._sticky_message_ids[key] = sent_message.id
            signature = self._make_signature(content, embed)
            self._record_sticky_signature(guild.id, channel.id, signature)
            await self.storage.upsert_sticky_runtime(guild.id, channel.id, sent_message.id, signature)

    async def _is_self_sticky_message(self, message: discord.Message) -> bool:
        bot_user = getattr(self.bot, "user", None)
        bot_user_id = getattr(bot_user, "id", None)
        if isinstance(bot_user_id, int) and getattr(message.author, "id", None) == bot_user_id:
            return True

        webhook_id = getattr(message, "webhook_id", None)
        if isinstance(webhook_id, int):
            rows = await self.storage.fetch_utility_webhooks(guild_id=message.guild.id, limit=500)
            for row in rows:
                if row.channel_id == message.channel.id and row.webhook_id == webhook_id:
                    return True

        if getattr(message.author, "bot", False) or isinstance(webhook_id, int):
            signature = self._message_signature(message)
            if signature is not None:
                key = (message.guild.id, message.channel.id)
                known = self._sticky_message_signatures.get(key, [])
                if signature in known:
                    return True

        if getattr(message.author, "bot", False) or isinstance(webhook_id, int):
            config = await self._load_running_section(message.guild.id, "sticky-message")
            items = self._sticky_items_from_config(config) if isinstance(config, dict) else []
            if self._matches_any_sticky_signature(message, items):
                return True

        message_id = getattr(message, "id", None)
        if not isinstance(message_id, int):
            return False
        key = (message.guild.id, message.channel.id)
        sticky_id = self._sticky_message_ids.get(key)
        return sticky_id == message_id

    def _matches_any_sticky_signature(self, message: discord.Message, items: list[dict[str, Any]]) -> bool:
        message_content = str(getattr(message, "content", "") or "")
        message_embed = self._first_embed_signature(getattr(message, "embeds", []) or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            channel_entry = self._find_sticky_channel(item.get("channels", []), message.channel.id)
            if channel_entry is None:
                continue
            expected_content = str(item.get("message", "") or "")
            expected_embed = self._first_embed_signature([self._build_sticky_embed(item)])
            if message_content == expected_content and message_embed == expected_embed:
                return True
        return False

    def _first_embed_signature(self, embeds: Any) -> dict[str, Any] | None:
        if not isinstance(embeds, list) or not embeds:
            return None
        first = embeds[0]
        if first is None:
            return None
        to_dict = getattr(first, "to_dict", None)
        if callable(to_dict):
            try:
                return to_dict()
            except Exception:
                logger.warning("sticky embed signature conversion failed", exc_info=True)
                return None
        return None

    def _record_sticky_signature(self, guild_id: int, channel_id: int, signature: str) -> None:
        key = (guild_id, channel_id)
        rows = list(self._sticky_message_signatures.get(key, []))
        rows.append(signature)
        if len(rows) > 20:
            rows = rows[-20:]
        self._sticky_message_signatures[key] = rows

    def _message_signature(self, message: discord.Message) -> str | None:
        content = str(getattr(message, "content", "") or "")
        embed_obj = self._first_embed_signature(getattr(message, "embeds", []) or [])
        return self._make_signature(content, self._embed_from_dict(embed_obj))

    def _make_signature(self, content: str, embed: discord.Embed | None) -> str:
        embed_dict = embed.to_dict() if embed is not None else None
        return json.dumps({"content": content, "embed": embed_dict}, sort_keys=True, ensure_ascii=False)

    def _embed_from_dict(self, embed: dict[str, Any] | None) -> discord.Embed | None:
        if not isinstance(embed, dict):
            return None
        try:
            return discord.Embed.from_dict(embed)
        except Exception:
            logger.warning("sticky embed reconstruction failed", exc_info=True)
            return None

    def _sticky_items_from_config(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        items = config.get("items", [])
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        legacy_keys = {"message", "delay", "trigger_bot_message", "channels", "embed"}
        if legacy_keys.intersection(config.keys()):
            return [
                {
                    "id": 1,
                    "message": config.get("message", ""),
                    "delay": config.get("delay", 0),
                    "trigger_bot_message": config.get("trigger_bot_message", False),
                    "channels": config.get("channels", []),
                    "embed": config.get("embed", {}),
                }
            ]
        return []

    async def _send_sticky_via_webhook(
        self,
        guild_id: int,
        channel_id: int,
        channel_entry: dict[str, Any],
        content: str,
        embed: discord.Embed | None,
    ) -> discord.Message | None:
        webhook_cfg = channel_entry.get("webhook", {})
        if not isinstance(webhook_cfg, dict):
            return None
        ref_id = webhook_cfg.get("webhook")
        if not isinstance(ref_id, str) or ref_id == "":
            return None

        row = await self.storage.get_utility_webhook(ref_id)
        if row is None or row.guild_id != guild_id or row.channel_id != channel_id:
            return None
        webhook = discord.Webhook.partial(row.webhook_id, row.webhook_token, client=self.bot)
        username = str(webhook_cfg.get("name", "") or "").strip() or None
        avatar_url = webhook_cfg.get("icon")
        if isinstance(avatar_url, str):
            avatar_url = avatar_url.strip() or None
        else:
            avatar_url = None
        try:
            return await webhook.send(
                content=content or None,
                embed=embed,
                wait=True,
                username=username,
                avatar_url=avatar_url,
            )
        except discord.HTTPException:
            return None

    async def _handle_auto_reaction(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        config = await self._load_running_section(message.guild.id, "auto-reaction")
        rules = config.get("rules", []) if isinstance(config, dict) else []
        if not isinstance(rules, list):
            return
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if not self._rule_matches_channel(rule, message.channel.id):
                continue
            emojis = rule.get("emojis", [])
            if not isinstance(emojis, list):
                continue
            for emoji_value in emojis:
                reaction = self._resolve_reaction_emoji(message.guild, str(emoji_value))
                if reaction is None:
                    continue
                await self.tick_meter.consume(message.guild.id, "auto_reaction.add", amount=1, stoppable=False)
                try:
                    await message.add_reaction(reaction)
                except discord.HTTPException:
                    continue

    async def _load_running_section(self, guild_id: int, section: str) -> dict[str, Any]:
        row = await self.storage.load_config("guild", guild_id, section)
        if row is None or not isinstance(row.data, dict):
            return {}
        payload = row.data.get("payload", row.data)
        if not isinstance(payload, dict):
            return {}
        running = payload.get("running_payload")
        if isinstance(running, dict):
            return dict(running)
        return dict(payload)

    async def _ensure_bind_ready(self) -> None:
        if hasattr(self.bot, "ensure_config_bound"):
            await self.bot.ensure_config_bound()
        bind_event = getattr(self.bot, "config_bind_ready", None)
        if bind_event is not None:
            await bind_event.wait()

    def _find_sticky_channel(self, channels: Any, channel_id: int) -> dict[str, Any] | None:
        if not isinstance(channels, list):
            return None
        for entry in channels:
            if not isinstance(entry, dict):
                continue
            if _safe_int(entry.get("channel_id")) == channel_id:
                return entry
        return None

    def _build_sticky_embed(self, config: dict[str, Any]) -> discord.Embed | None:
        embed_cfg = config.get("embed", {})
        if not isinstance(embed_cfg, dict):
            return None

        title = str(embed_cfg.get("title", "") or "")
        description = str(embed_cfg.get("description", "") or "")
        footer = str(embed_cfg.get("footer", "") or "")
        color = self._parse_color(embed_cfg.get("color"))
        has_field_values = bool(title or description or footer)

        fields = embed_cfg.get("fields", [])
        parsed_fields: list[tuple[str, str, bool]] = []
        if isinstance(fields, list):
            for item in sorted((f for f in fields if isinstance(f, dict)), key=lambda it: _safe_int(it.get("id"), 0)):
                name = str(item.get("name", "") or "")
                value = str(item.get("value", "") or "")
                inline_mode = bool(item.get("inline_mode", False))
                if name or value:
                    parsed_fields.append((name, value, inline_mode))
            if parsed_fields:
                has_field_values = True

        if not has_field_values and color is None:
            return None

        embed = discord.Embed(
            title=title if title else None,
            description=description if description else None,
            color=color or discord.Color.blurple(),
        )
        for name, value, inline_mode in parsed_fields:
            embed.add_field(name=name or "-", value=value or "-", inline=inline_mode)
        if footer:
            embed.set_footer(text=footer)
        avatar_url = embed_cfg.get("avatar_url")
        if isinstance(avatar_url, str) and avatar_url.strip():
            embed.set_thumbnail(url=avatar_url.strip())
        return embed

    def _parse_color(self, raw: Any) -> discord.Color | None:
        if raw is None:
            return None
        value = str(raw).strip().lower()
        if value == "":
            return None
        if value in _COLOR_MAP:
            return discord.Color(_COLOR_MAP[value])
        if value.startswith("0x"):
            try:
                return discord.Color(int(value, 16))
            except ValueError:
                return None
        try:
            return discord.Color(int(value))
        except ValueError:
            return None

    def _rule_matches_channel(self, rule: dict[str, Any], channel_id: int) -> bool:
        channels = rule.get("channels", [])
        if not isinstance(channels, Sequence):
            return False
        return channel_id in [_safe_int(ch) for ch in channels if _safe_int(ch) is not None]

    def _resolve_reaction_emoji(self, guild: discord.Guild, raw: str) -> str | discord.Emoji | None:
        raw = raw.strip()
        if raw == "":
            return None
        match = _CUSTOM_EMOJI_RE.match(raw)
        if match:
            emoji_id = int(match.group(1))
            emoji = guild.get_emoji(emoji_id)
            return emoji
        return raw


async def setup(bot: commands.Bot):
    await bot.add_cog(StickyAutoCog(bot))


def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
