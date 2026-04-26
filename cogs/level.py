from __future__ import annotations

from datetime import datetime, timezone
from math import floor

import discord
from discord.ext import commands

from utils.config_runtime import ensure_bind_ready
from utils.discord_helpers import resolve_guild_channel
from utils.level import LevelEventContext, LevelService
from utils.storage import Storage
from utils.tick import TickMeter


class LevelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.storage = Storage()
        self.tick_meter = getattr(bot, "tick_meter", TickMeter(self.storage))
        self.service = LevelService(self.storage, tick_meter=self.tick_meter)

    async def cog_load(self) -> None:
        await self.storage.init_schema()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._ensure_bind_ready()
        if hasattr(self.bot, "ensure_config_bound"):
            await self.bot.ensure_config_bound()

    @commands.command(name="rank")
    async def rank(self, ctx: commands.Context) -> None:
        await self._ensure_bind_ready()
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("guild-only command")
            return
        snapshot = await self.service.get_rank_snapshot(ctx.guild.id, ctx.author.id)
        embed = await self._build_rank_embed(ctx.guild.id, snapshot)
        await ctx.send(content=f"{ctx.author.mention}", embed=embed)

    @commands.command(name="ranking")
    async def ranking(self, ctx: commands.Context, limit: int = 10) -> None:
        await self._ensure_bind_ready()
        if ctx.guild is None:
            await ctx.send("guild-only command")
            return
        limit = max(1, min(limit, 50))
        rows = await self.storage.fetch_level_ranking(ctx.guild.id, limit)
        if not rows:
            await ctx.send("ranking: (empty)")
            return
        lines = ["ranking:"]
        for index, row in enumerate(rows, start=1):
            lines.append(f"{index}. user={row.user_id} level={row.level} total_xp={row.total_xp}")
        await ctx.send("```text\n" + "\n".join(lines) + "\n```")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._ensure_bind_ready()
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return

        ctx = LevelEventContext(
            guild_id=message.guild.id,
            user_id=message.author.id,
            event_type="message",
            channel_id=message.channel.id,
            role_ids=[role.id for role in message.author.roles],
            occurred_at=datetime.now(timezone.utc),
            message_length=len(message.content or ""),
        )
        result = await self.service.apply_event(ctx)
        if result.leveled_up:
            await self._maybe_notify_levelup(message.guild, message.author, result.old_level, result.new_level, result.new_total_xp)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._ensure_bind_ready()
        if payload.guild_id is None:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(payload.guild_id)
            except discord.HTTPException:
                return

        member = payload.member
        if member is None:
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return
        if member.bot:
            return

        ctx = LevelEventContext(
            guild_id=guild.id,
            user_id=member.id,
            event_type="reaction",
            channel_id=payload.channel_id,
            role_ids=[role.id for role in member.roles],
            occurred_at=datetime.now(timezone.utc),
        )
        result = await self.service.apply_event(ctx)
        if result.leveled_up:
            await self._maybe_notify_levelup(guild, member, result.old_level, result.new_level, result.new_total_xp)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        await self._ensure_bind_ready()
        if member.guild is None or member.bot:
            return
        now = datetime.now(timezone.utc)
        role_ids = [role.id for role in member.roles]
        guild_id = member.guild.id

        if before.channel is None and after.channel is not None:
            await self.service.mark_voice_join(guild_id, member.id, now)
            return

        if before.channel is not None and after.channel is None:
            result = await self.service.apply_voice_leave(guild_id, member.id, before.channel.id, role_ids, now)
            if result.leveled_up:
                await self._maybe_notify_levelup(member.guild, member, result.old_level, result.new_level, result.new_total_xp)
            return

        if before.channel is not None and after.channel is not None and before.channel.id != after.channel.id:
            result = await self.service.apply_voice_leave(guild_id, member.id, before.channel.id, role_ids, now)
            await self.service.mark_voice_join(guild_id, member.id, now)
            if result.leveled_up:
                await self._maybe_notify_levelup(member.guild, member, result.old_level, result.new_level, result.new_total_xp)

    async def _maybe_notify_levelup(
        self,
        guild: discord.Guild,
        member: discord.Member,
        old_level: int,
        new_level: int,
        total_xp: int,
    ) -> None:
        level_common = await self.service._load_running_section(guild.id, "level-common")
        channel_id = level_common.get("levelup_channel")
        if not isinstance(channel_id, int):
            return
        channel = await resolve_guild_channel(guild, channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        await self.tick_meter.consume(guild.id, "log.discord.levelup", amount=1, stoppable=True)
        message = self._render_levelup_message(
            level_common=level_common,
            member=member,
            old_level=old_level,
            new_level=new_level,
            total_xp=total_xp,
        )
        await channel.send(message)

    async def _ensure_bind_ready(self) -> None:
        await ensure_bind_ready(self.bot)

    async def _build_rank_embed(self, guild_id: int, snapshot: dict) -> discord.Embed:
        level = int(snapshot.get("level", 0))
        total_xp = int(snapshot.get("total_xp", 0))
        next_level_xp = snapshot.get("next_level_xp")
        rank = snapshot.get("rank")

        current_floor, next_threshold = await self._resolve_level_progress_bounds(guild_id, total_xp, level, next_level_xp)
        progress_percent = self._progress_percent(total_xp, current_floor, next_threshold)
        progress_bar = self._progress_bar(progress_percent)
        rank_text = str(rank) if rank is not None else "-"
        next_text = "MAX" if next_threshold is None else str(next_threshold)
        remain_text = "-" if next_threshold is None else str(max(0, next_threshold - total_xp))

        embed = discord.Embed(title="Level Status", color=discord.Color.blurple())
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="Total XP", value=str(total_xp), inline=True)
        embed.add_field(name="Rank", value=rank_text, inline=True)
        embed.add_field(
            name="Progress",
            value=f"`{progress_bar}` {progress_percent:.1f}%\nnext={next_text} remain={remain_text}",
            inline=False,
        )
        return embed

    async def _resolve_level_progress_bounds(
        self, guild_id: int, total_xp: int, level: int, next_level_xp: int | None
    ) -> tuple[int, int | None]:
        rows = await self.storage.fetch_level_table(guild_id, limit=10000)
        if rows:
            floor_xp = 0
            ceil_xp: int | None = None
            for row in rows:
                threshold = int(row.get("required_total_xp", 0))
                if threshold <= total_xp:
                    floor_xp = threshold
                elif ceil_xp is None:
                    ceil_xp = threshold
                    break
            if ceil_xp is None:
                ceil_xp = int(next_level_xp) if isinstance(next_level_xp, int) else None
            return floor_xp, ceil_xp

        default_next = int(next_level_xp) if isinstance(next_level_xp, int) else (level + 1) * 100
        return max(0, level * 100), default_next

    def _progress_percent(self, total_xp: int, floor_xp: int, next_xp: int | None) -> float:
        if next_xp is None or next_xp <= floor_xp:
            return 100.0
        span = next_xp - floor_xp
        done = max(0, min(total_xp - floor_xp, span))
        return round((done / span) * 100, 1)

    def _progress_bar(self, percent: float, width: int = 20) -> str:
        clamped = max(0.0, min(percent, 100.0))
        filled = floor((clamped / 100.0) * width)
        return "█" * filled + "░" * (width - filled)

    def _render_levelup_message(
        self,
        *,
        level_common: dict,
        member: discord.Member,
        old_level: int,
        new_level: int,
        total_xp: int,
    ) -> str:
        template = level_common.get("levelup_message")
        if not isinstance(template, str) or template == "":
            template = "{mention} leveled up: {old_level} -> {level} (xp={total_xp})"

        replacements = {
            "mention": member.mention,
            "level": str(new_level),
            "old_level": str(old_level),
            "total_xp": str(total_xp),
            "username": member.name,
            "user": str(member),
            "nickname": member.nick if member.nick else member.name,
        }
        rendered = template
        for key, value in replacements.items():
            rendered = rendered.replace(f"{{{key}}}", value)
        return rendered


async def setup(bot: commands.Bot):
    await bot.add_cog(LevelCog(bot))
