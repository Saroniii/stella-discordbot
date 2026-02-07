from __future__ import annotations

import os

import discord
from discord.ext import commands

from utils.cli.engine import CliEngine
from utils.cli.session import SessionRegistry
from utils.cli.types import EngineContext
from utils.storage import Storage


SESSION_TIMEOUT_SEC = 600


class CliCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.storage = Storage()
        self.engine = CliEngine(self.storage, crash_notifier=self._send_crash_report)
        self.sessions = SessionRegistry()

        admin_guild_raw = os.getenv("ADMIN_GUILD_ID")
        self.admin_guild_id = int(admin_guild_raw) if admin_guild_raw and admin_guild_raw.isdigit() else None
        roles_raw = os.getenv("BOT_ADMIN_ROLE_IDS", "")
        self.admin_role_ids = {int(value.strip()) for value in roles_raw.split(",") if value.strip().isdigit()}

    async def cog_load(self) -> None:
        await self.storage.init_schema()

    async def _is_bot_admin(self, member: discord.Member) -> bool:
        if not self.admin_guild_id or not self.admin_role_ids:
            return False
        admin_guild = self.bot.get_guild(self.admin_guild_id)
        if admin_guild is None:
            return False
        admin_member = admin_guild.get_member(member.id)
        if admin_member is None:
            try:
                admin_member = await admin_guild.fetch_member(member.id)
            except discord.HTTPException:
                return False
        member_role_ids = {role.id for role in admin_member.roles}
        return bool(member_role_ids & self.admin_role_ids)

    @commands.command(name="cli")
    async def cli(self, ctx: commands.Context) -> None:
        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.send("This command is guild-only.")
            return

        if not ctx.author.guild_permissions.manage_guild:
            await ctx.send("Manage Guild permission is required.")
            return

        existing = self.sessions.get(ctx.guild.id)
        if existing and not self.sessions.is_expired(ctx.guild.id, SESSION_TIMEOUT_SEC):
            await ctx.send(f"An active CLI session already exists: <#{existing.thread_id}>")
            return
        if existing:
            self.sessions.release(ctx.guild.id)

        if not hasattr(ctx.message, "create_thread"):
            await ctx.send("Failed to create thread for CLI.")
            return

        try:
            thread = await ctx.message.create_thread(name=f"stella-cli-{ctx.author.display_name}")
        except discord.HTTPException as exc:
            await ctx.send(f"Failed to create thread: {exc}")
            return

        engine_ctx = EngineContext(
            actor_user_id=ctx.author.id,
            guild_id=ctx.guild.id,
            channel_id=thread.id,
            is_bot_admin=await self._is_bot_admin(ctx.author),
            has_manage_guild=ctx.author.guild_permissions.manage_guild,
        )

        session, initial = await self.engine.initialize_session(engine_ctx)
        session.thread_id = thread.id
        if not self.sessions.acquire(ctx.guild.id, session):
            await thread.send("Failed to acquire session lock.")
            return

        await thread.send(self._format_output(initial.output, initial.prompt))

        try:
            while True:
                try:
                    message = await self.bot.wait_for(
                        "message",
                        timeout=SESSION_TIMEOUT_SEC,
                        check=lambda m: m.author.id == ctx.author.id and m.channel.id == thread.id,
                    )
                except TimeoutError:
                    await thread.send("Session timeout. CLI closed.")
                    break

                self.sessions.touch(ctx.guild.id)
                line = message.content.strip()
                session, result = await self.engine.execute(engine_ctx, session, line)
                await thread.send(self._format_output(result.output, result.prompt))
                if result.should_exit:
                    break
        finally:
            self.sessions.release(ctx.guild.id)

    def _format_output(self, output: str, prompt: str) -> str:
        payload = output.strip()
        if payload:
            return f"```text\n{payload}\n{prompt}\n```"
        return f"```text\n{prompt}\n```"

    async def _send_crash_report(self, channel_id: int, message: str) -> str:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException as exc:
                return f"drop(fetch-failed:{exc.__class__.__name__})"
        try:
            await channel.send(f"```text\n{message}\n```")
        except discord.HTTPException as exc:
            return f"drop(send-failed:{exc.__class__.__name__})"
        return "sent"


async def setup(bot: commands.Bot):
    await bot.add_cog(CliCog(bot))
