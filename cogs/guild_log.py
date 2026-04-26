from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord
from discord.ext import commands

from utils.config_runtime import ensure_bind_ready, extract_running_payload
from utils.discord_helpers import discord_timestamp_from_datetime, discord_timestamp_now, resolve_guild_channel, trim_text
from utils.guild_log_cache import CachedMessage, guild_message_cache
from utils.storage import Storage
from utils.tick import TickMeter


@dataclass
class _GuildLogConfig:
    mod_log_channel: int | None
    mod_log_types: set[str]
    message_log_channel: int | None
    message_log_categories: set[str]
    message_tracking_limit: int
    message_tracking_mode: str
    member_log_channel: int | None
    member_log_categories: set[str]


class GuildLogCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.storage = Storage()
        self.tick_meter = getattr(bot, "tick_meter", TickMeter(self.storage))

    async def cog_load(self) -> None:
        await self.storage.init_schema()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._ensure_bind_ready()
        if message.guild is None or message.author.bot:
            return
        config = await self._load_config(message.guild.id)
        if config.message_tracking_mode == "normal":
            guild_message_cache.clear_guild(message.guild.id)
            return
        if config.message_tracking_mode == "extra":
            await self._cache_put(
                guild_id=message.guild.id,
                message=CachedMessage(
                    message_id=message.id,
                    channel_id=message.channel.id,
                    author_id=message.author.id,
                    author_name=message.author.name,
                    content=message.content or "",
                ),
                limit=config.message_tracking_limit,
            )

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        await self._ensure_bind_ready()
        if message.guild is None or message.author.bot:
            return
        if not await self.tick_meter.start_work(message.guild.id, "log.event.message.delete.entry", stoppable=True):
            return

        config = await self._load_config(message.guild.id)
        if config.message_log_channel is None or "delete" not in config.message_log_categories:
            return
        content = _trim_text(message.content or "(no content)")
        fields = [
            ("Channel", f"<#{message.channel.id}>", True),
            ("Author", f"{message.author.mention} (`{message.author.id}`)", False),
            ("Message ID", str(message.id), True),
            ("Content", content, False),
            ("At", _discord_timestamp_now(), True),
        ]
        await self._send_log_embed(
            message.guild,
            config.message_log_channel,
            title="Message Deleted",
            color=discord.Color.red(),
            fields=fields,
            tick_source="log.event.message.delete.send",
        )
        if config.message_tracking_mode == "extra":
            await self._cache_pop(message.guild.id, message.id)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        await self._ensure_bind_ready()
        if before.guild is None or before.author.bot:
            return
        if before.content == after.content:
            return
        if not await self.tick_meter.start_work(before.guild.id, "log.event.message.edit.entry", stoppable=True):
            return

        config = await self._load_config(before.guild.id)
        if config.message_log_channel is None or "edit" not in config.message_log_categories:
            return
        before_text = _trim_text(before.content or "(no content)")
        after_text = _trim_text(after.content or "(no content)")
        fields = [
            ("Channel", f"<#{before.channel.id}>", True),
            ("Author", f"{before.author.mention} (`{before.author.id}`)", False),
            ("Message ID", str(before.id), True),
            ("Before", before_text, False),
            ("After", after_text, False),
            ("At", _discord_timestamp_now(), True),
        ]
        await self._send_log_embed(
            before.guild,
            config.message_log_channel,
            title="Message Edited",
            color=discord.Color.orange(),
            fields=fields,
            tick_source="log.event.message.edit.send",
        )
        config = await self._load_config(before.guild.id)
        if config.message_tracking_mode == "extra":
            await self._cache_put(
                guild_id=before.guild.id,
                message=CachedMessage(
                    message_id=after.id,
                    channel_id=after.channel.id,
                    author_id=after.author.id,
                    author_name=after.author.name,
                    content=after.content or "",
                ),
                limit=config.message_tracking_limit,
            )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        await self._ensure_bind_ready()
        if payload.guild_id is None:
            return
        if payload.cached_message is not None:
            return
        guild = await self._resolve_guild(payload.guild_id)
        if guild is None:
            return
        if not await self.tick_meter.start_work(guild.id, "log.event.message.delete.raw.entry", stoppable=True):
            return
        config = await self._load_config(guild.id)
        if config.message_log_channel is None or "delete" not in config.message_log_categories:
            return
        if config.message_tracking_mode != "extra":
            return
        cached = await self._cache_pop(guild.id, payload.message_id)
        author_text = f"<@{cached.author_id}> (`{cached.author_id}`)" if cached else "unknown"
        content = _trim_text(cached.content if cached else "(content unavailable)")
        fields = [
            ("Channel", f"<#{payload.channel_id}>", True),
            ("Author", author_text, False),
            ("Message ID", str(payload.message_id), True),
            ("Content", content, False),
            ("At", _discord_timestamp_now(), True),
        ]
        await self._send_log_embed(
            guild,
            config.message_log_channel,
            title="Message Deleted",
            color=discord.Color.red(),
            fields=fields,
            tick_source="log.event.message.delete.raw.send",
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        await self._ensure_bind_ready()
        if payload.guild_id is None:
            return
        if payload.cached_message is not None:
            return
        if "content" not in payload.data:
            return
        guild = await self._resolve_guild(payload.guild_id)
        if guild is None:
            return
        if not await self.tick_meter.start_work(guild.id, "log.event.message.edit.raw.entry", stoppable=True):
            return
        config = await self._load_config(guild.id)
        if config.message_log_channel is None or "edit" not in config.message_log_categories:
            return
        if config.message_tracking_mode != "extra":
            return
        cached = await self._cache_get(guild.id, payload.message_id)
        if cached is None:
            return
        new_content_raw = payload.data.get("content")
        if not isinstance(new_content_raw, str):
            return
        if cached.content == new_content_raw:
            return
        author_text = f"<@{cached.author_id}> (`{cached.author_id}`)"
        fields = [
            ("Channel", f"<#{cached.channel_id}>", True),
            ("Author", author_text, False),
            ("Message ID", str(payload.message_id), True),
            ("Before", _trim_text(cached.content or "(no content)"), False),
            ("After", _trim_text(new_content_raw or "(no content)"), False),
            ("At", _discord_timestamp_now(), True),
        ]
        await self._send_log_embed(
            guild,
            config.message_log_channel,
            title="Message Edited",
            color=discord.Color.orange(),
            fields=fields,
            tick_source="log.event.message.edit.raw.send",
        )
        await self._cache_put(
            guild_id=guild.id,
            message=CachedMessage(
                message_id=payload.message_id,
                channel_id=cached.channel_id,
                author_id=cached.author_id,
                author_name=cached.author_name,
                content=new_content_raw,
            ),
            limit=config.message_tracking_limit,
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._ensure_bind_ready()
        if member.guild is None or member.bot:
            return
        if not await self.tick_meter.start_work(member.guild.id, "log.event.member.join.entry", stoppable=True):
            return
        config = await self._load_config(member.guild.id)
        if config.member_log_channel is None or "join" not in config.member_log_categories:
            return
        fields = [
            ("User", f"{member.mention} (`{member.id}`)", False),
            ("Username", member.name, True),
            ("At", _discord_timestamp_now(), True),
        ]
        await self._send_log_embed(
            member.guild,
            config.member_log_channel,
            title="Member Joined",
            color=discord.Color.green(),
            fields=fields,
            tick_source="log.event.member.join.send",
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        await self._ensure_bind_ready()
        if member.guild is None or member.bot:
            return
        if not await self.tick_meter.start_work(member.guild.id, "log.event.member.remove.entry", stoppable=True):
            return
        config = await self._load_config(member.guild.id)
        if config.member_log_channel is not None and "leave" in config.member_log_categories:
            fields = [
                ("User", f"{member.mention} (`{member.id}`)", False),
                ("Username", member.name, True),
                ("At", _discord_timestamp_now(), True),
            ]
            await self._send_log_embed(
                member.guild,
                config.member_log_channel,
                title="Member Left",
                color=discord.Color.dark_orange(),
                fields=fields,
                tick_source="log.event.member.leave.send",
            )
        if config.mod_log_channel is not None and "kick" in config.mod_log_types:
            kicked, actor = await self._detect_kick(member.guild, member.id)
            if kicked:
                fields = [
                    ("Target", f"{member.mention} (`{member.id}`)", False),
                    ("Moderator", actor, False),
                    ("At", _discord_timestamp_now(), True),
                ]
                await self._send_log_embed(
                    member.guild,
                    config.mod_log_channel,
                    title="Member Kicked",
                    color=discord.Color.dark_red(),
                    fields=fields,
                    tick_source="log.event.mod.kick.send",
                )

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User) -> None:
        await self._ensure_bind_ready()
        if user.bot:
            return
        if not await self.tick_meter.start_work(guild.id, "log.event.mod.ban.entry", stoppable=True):
            return
        config = await self._load_config(guild.id)
        if config.mod_log_channel is None or "ban" not in config.mod_log_types:
            return
        fields = [
            ("Target", f"{user.mention} (`{user.id}`)", False),
            ("At", _discord_timestamp_now(), True),
        ]
        await self._send_log_embed(
            guild,
            config.mod_log_channel,
            title="Member Banned",
            color=discord.Color.red(),
            fields=fields,
            tick_source="log.event.mod.ban.send",
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        await self._ensure_bind_ready()
        if user.bot:
            return
        if not await self.tick_meter.start_work(guild.id, "log.event.mod.unban.entry", stoppable=True):
            return
        config = await self._load_config(guild.id)
        if config.mod_log_channel is None or "unban" not in config.mod_log_types:
            return
        fields = [
            ("Target", f"{user.mention} (`{user.id}`)", False),
            ("At", _discord_timestamp_now(), True),
        ]
        await self._send_log_embed(
            guild,
            config.mod_log_channel,
            title="Member Unbanned",
            color=discord.Color.green(),
            fields=fields,
            tick_source="log.event.mod.unban.send",
        )

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        await self._ensure_bind_ready()
        if before.guild is None or before.bot:
            return
        if not await self.tick_meter.start_work(before.guild.id, "log.event.member.update.entry", stoppable=True):
            return
        config = await self._load_config(before.guild.id)
        if config.member_log_channel is not None:
            if "nickname" in config.member_log_categories and before.nick != after.nick:
                await self._send_log_embed(
                    before.guild,
                    config.member_log_channel,
                    title="Member Nickname Updated",
                    color=discord.Color.blurple(),
                    fields=[
                        ("User", f"{after.mention} (`{after.id}`)", False),
                        ("Before", before.nick or before.name, True),
                        ("After", after.nick or after.name, True),
                        ("At", _discord_timestamp_now(), True),
                    ],
                    tick_source="log.event.member.update.send",
                )

            if "role" in config.member_log_categories:
                before_ids = {role.id for role in before.roles}
                after_ids = {role.id for role in after.roles}
                if before_ids != after_ids:
                    added = sorted(after_ids - before_ids)
                    removed = sorted(before_ids - after_ids)
                    await self._send_log_embed(
                        before.guild,
                        config.member_log_channel,
                        title="Member Roles Updated",
                        color=discord.Color.gold(),
                        fields=[
                            ("User", f"{after.mention} (`{after.id}`)", False),
                            ("Added", " ".join(str(v) for v in added) if added else "-", True),
                            ("Removed", " ".join(str(v) for v in removed) if removed else "-", True),
                            ("At", _discord_timestamp_now(), True),
                        ],
                        tick_source="log.event.member.update.send",
                    )

            if "avatar" in config.member_log_categories:
                before_avatar = str(before.display_avatar.url) if before.display_avatar else ""
                after_avatar = str(after.display_avatar.url) if after.display_avatar else ""
                if before_avatar != after_avatar:
                    await self._send_log_embed(
                        before.guild,
                        config.member_log_channel,
                        title="Member Avatar Updated",
                        color=discord.Color.purple(),
                        fields=[
                            ("User", f"{after.mention} (`{after.id}`)", False),
                            ("Before", before_avatar or "(none)", False),
                            ("After", after_avatar or "(none)", False),
                            ("At", _discord_timestamp_now(), True),
                        ],
                        tick_source="log.event.member.update.send",
                    )

        if config.mod_log_channel is not None and "timeout" in config.mod_log_types:
            before_timeout = before.communication_disabled_until
            after_timeout = after.communication_disabled_until
            before_active = bool(before_timeout and before_timeout.astimezone(timezone.utc) > datetime.now(timezone.utc))
            after_active = bool(after_timeout and after_timeout.astimezone(timezone.utc) > datetime.now(timezone.utc))
            if before_active != after_active:
                title = "Member Timed Out" if after_active else "Member Timeout Removed"
                await self._send_log_embed(
                    before.guild,
                    config.mod_log_channel,
                    title=title,
                    color=discord.Color.red() if after_active else discord.Color.green(),
                    fields=[
                        ("User", f"{after.mention} (`{after.id}`)", False),
                        (
                            "Until",
                            _discord_timestamp_from_datetime(after_timeout.astimezone(timezone.utc))
                            if after_active and after_timeout is not None
                            else "-",
                            True,
                        ),
                        ("At", _discord_timestamp_now(), True),
                    ],
                    tick_source="log.event.mod.timeout.send",
                )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        await self._ensure_bind_ready()
        if member.guild is None or member.bot:
            return
        if not await self.tick_meter.start_work(member.guild.id, "log.event.mod.voice.entry", stoppable=True):
            return
        config = await self._load_config(member.guild.id)
        if config.mod_log_channel is None:
            return
        if before.mute != after.mute:
            event_type = "mute" if after.mute else "unmute"
            if event_type not in config.mod_log_types:
                return
            await self._send_log_embed(
                member.guild,
                config.mod_log_channel,
                title="Member Server Muted" if after.mute else "Member Server Unmuted",
                color=discord.Color.orange() if after.mute else discord.Color.green(),
                fields=[
                    ("User", f"{member.mention} (`{member.id}`)", False),
                    ("At", _discord_timestamp_now(), True),
                ],
                tick_source="log.event.mod.voice.send",
            )

    async def _ensure_bind_ready(self) -> None:
        await ensure_bind_ready(self.bot)

    async def _load_config(self, guild_id: int) -> _GuildLogConfig:
        stored = await self.storage.load_config("guild", guild_id, "guild-log")
        if stored is None:
            return _GuildLogConfig(None, set(), None, set(), 1000, "normal", None, set())
        payload = extract_running_payload(stored.data)
        mod_log = payload.get("mod_log", {}) if isinstance(payload, dict) else {}
        message_log = payload.get("message_log", {}) if isinstance(payload, dict) else {}
        member_log = payload.get("member_log", {}) if isinstance(payload, dict) else {}
        return _GuildLogConfig(
            mod_log_channel=mod_log.get("channel") if isinstance(mod_log.get("channel"), int) else None,
            mod_log_types=set(mod_log.get("types", [])) if isinstance(mod_log.get("types"), list) else set(),
            message_log_channel=message_log.get("channel") if isinstance(message_log.get("channel"), int) else None,
            message_log_categories=set(message_log.get("categories", [])) if isinstance(message_log.get("categories"), list) else set(),
            message_tracking_limit=message_log.get("tracking_message_count")
            if isinstance(message_log.get("tracking_message_count"), int)
            else 1000,
            message_tracking_mode=message_log.get("tracking_message_mode")
            if isinstance(message_log.get("tracking_message_mode"), str) and message_log.get("tracking_message_mode") in {"normal", "extra"}
            else "normal",
            member_log_channel=member_log.get("channel") if isinstance(member_log.get("channel"), int) else None,
            member_log_categories=set(member_log.get("categories", [])) if isinstance(member_log.get("categories"), list) else set(),
        )

    async def _send_log_embed(
        self,
        guild: discord.Guild,
        channel_id: int,
        *,
        title: str,
        color: discord.Color,
        fields: list[tuple[str, str, bool]],
        tick_source: str,
    ) -> None:
        if not await self.tick_meter.consume(guild.id, tick_source, amount=1, stoppable=True):
            return
        channel = await resolve_guild_channel(guild, channel_id)
        send = getattr(channel, "send", None)
        if send is None:
            return
        embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
        for name, value, inline in fields:
            embed.add_field(name=name, value=trim_text(value, limit=1000), inline=inline)
        try:
            await send(embed=embed)
        except discord.HTTPException:
            return

    async def _detect_kick(self, guild: discord.Guild, target_user_id: int) -> tuple[bool, str]:
        if not hasattr(guild, "audit_logs"):
            return False, "-"
        now = datetime.now(timezone.utc)
        try:
            async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.kick):
                target = getattr(entry, "target", None)
                if target is None or getattr(target, "id", None) != target_user_id:
                    continue
                created = getattr(entry, "created_at", None)
                if created is not None:
                    created_utc = created.astimezone(timezone.utc)
                    if abs((now - created_utc).total_seconds()) > 20:
                        continue
                actor = getattr(entry, "user", None)
                actor_text = f"{getattr(actor, 'mention', '-')} (`{getattr(actor, 'id', '-')}`)" if actor else "-"
                return True, actor_text
        except (discord.HTTPException, discord.Forbidden):
            return False, "-"
        return False, "-"

    async def _resolve_guild(self, guild_id: int) -> discord.Guild | None:
        guild = self.bot.get_guild(guild_id)
        if guild is not None:
            return guild
        fetch = getattr(self.bot, "fetch_guild", None)
        if fetch is None:
            return None
        try:
            return await fetch(guild_id)
        except discord.HTTPException:
            return None

    async def _cache_put(self, guild_id: int, message: CachedMessage, limit: int) -> None:
        await self.tick_meter.consume(guild_id, "log.cache.put", amount=1, stoppable=False)
        guild_message_cache.put(guild_id, message, limit)

    async def _cache_get(self, guild_id: int, message_id: int) -> CachedMessage | None:
        await self.tick_meter.consume(guild_id, "log.cache.get", amount=1, stoppable=False)
        return guild_message_cache.get(guild_id, message_id)

    async def _cache_pop(self, guild_id: int, message_id: int) -> CachedMessage | None:
        await self.tick_meter.consume(guild_id, "log.cache.pop", amount=1, stoppable=False)
        return guild_message_cache.pop(guild_id, message_id)


def _trim_text(value: str, limit: int = 300) -> str:
    return trim_text(value, limit)


def _discord_timestamp_now() -> str:
    return discord_timestamp_now()


def _discord_timestamp_from_datetime(value: datetime) -> str:
    return discord_timestamp_from_datetime(value)


async def setup(bot: commands.Bot):
    await bot.add_cog(GuildLogCog(bot))
