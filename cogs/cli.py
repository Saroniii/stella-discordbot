from __future__ import annotations

import asyncio
import io
import os
import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import discord
from discord.ext import commands

from utils.cli.engine import CliEngine
from utils.cli.session import SessionRegistry
from utils.cli.types import EngineContext
from utils.config_bind import deploy_many_guilds, rebind_many_guilds
from utils.config_runtime import extract_running_payload
from utils.discord_helpers import resolve_guild_channel
from utils.storage import Storage
from utils.tick import TickMeter


SESSION_TIMEOUT_SEC = 600
THREAD_DELETE_DELAY_SEC = 10
CLI_THREAD_PREFIX = "stella-cli-"
_CUSTOM_EMOJI_RE = re.compile(r"^<a?:[a-zA-Z0-9_]+:(\d+)>$")


class CliCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.storage = Storage()
        self.tick_meter = getattr(bot, "tick_meter", TickMeter(self.storage))
        self.engine = CliEngine(
            self.storage,
            crash_notifier=self._send_crash_report,
            tick_meter=self.tick_meter,
            utils_executor=self._execute_utils,
            set_validator=self._validate_cli_set,
        )
        self.sessions = SessionRegistry()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._cli_log_streams: dict[str, io.StringIO] = {}
        self._cli_log_stop_requested: set[str] = set()
        self._cli_log_pending_files: dict[str, discord.File] = {}

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
        await self._ensure_global_bind()
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

        console_config = await self._load_console_config(ctx.guild.id)
        console_mode = str(console_config.get("console_mode", "thread"))
        auto_delete_thread = bool(console_config.get("thread_console_after_delete", False))
        target_channel = ctx.channel
        thread: discord.Thread | None = None
        if console_mode == "thread":
            if not hasattr(ctx.message, "create_thread"):
                await ctx.send("Failed to create thread for CLI.")
                return

            try:
                thread = await ctx.message.create_thread(name=f"{CLI_THREAD_PREFIX}{ctx.author.display_name}")
            except discord.HTTPException as exc:
                await ctx.send(f"Failed to create thread: {exc}")
                return
            target_channel = thread

        engine_ctx = EngineContext(
            actor_user_id=ctx.author.id,
            guild_id=ctx.guild.id,
            channel_id=target_channel.id,
            is_bot_admin=await self._is_bot_admin(ctx.author),
            has_manage_guild=ctx.author.guild_permissions.manage_guild,
            guild=ctx.guild,
        )

        session, initial = await self.engine.initialize_session(engine_ctx)
        session.thread_id = target_channel.id
        if not self.sessions.acquire(ctx.guild.id, session):
            await target_channel.send("Failed to acquire session lock.")
            return

        await self._send_formatted(target_channel, initial.output, initial.prompt)
        cleanup_last_message_id: int | None = None

        try:
            while True:
                try:
                    message = await self.bot.wait_for(
                        "message",
                        timeout=SESSION_TIMEOUT_SEC,
                        check=lambda m: m.author.id == ctx.author.id and m.channel.id == target_channel.id,
                    )
                except TimeoutError:
                    await target_channel.send("Session timeout. CLI closed.")
                    cleanup_last_message_id = getattr(target_channel, "last_message_id", None)
                    break

                self.sessions.touch(ctx.guild.id)
                should_exit = False
                pre_prompt = self.engine._prompt(session)
                line_results: list[tuple[str, object, bool]] = []
                for line in self._split_input_lines(message.content):
                    if getattr(self.bot, "system_reloading", False):
                        reload_result = self.engine._prompt(session)
                        line_results.append(
                            (
                                line,
                                SimpleNamespace(output="system is reloading, please retry shortly", prompt=reload_result, should_exit=False),
                                False,
                            )
                        )
                        continue
                    pre_prompt = self.engine._prompt(session)
                    session, result = await self.engine.execute(engine_ctx, session, line)
                    self._append_cli_log_entry(session, pre_prompt, line, result.output)
                    suppress_line = (
                        session.cli_log_stream_enabled
                        and session.cli_log_no_message_response
                        and not self._is_cli_log_stop_command(line)
                    )
                    line_results.append((line, result, suppress_line))
                    if session.session_id in self._cli_log_stop_requested:
                        cli_file = self._finalize_cli_log_to_file(session)
                        if cli_file is not None:
                            self._cli_log_pending_files[session.session_id] = cli_file
                    if result.should_exit:
                        should_exit = True
                        break

                suppressed_results = [result for _line, result, suppress in line_results if suppress]
                visible_results = [result for _line, result, suppress in line_results if not suppress]

                if suppressed_results:
                    success = all(self._is_success_output(result.output) for result in suppressed_results)
                    await self._add_completion_reaction(message, success, ctx.guild.id)
                for result in visible_results:
                    await self._send_formatted(target_channel, result.output, result.prompt)

                pending_file = self._cli_log_pending_files.pop(session.session_id, None)
                if pending_file is not None:
                    await target_channel.send(file=pending_file)

                if should_exit:
                    cleanup_last_message_id = getattr(target_channel, "last_message_id", None)
                    break
        finally:
            self._discard_cli_log_stream(session)
            self.sessions.release(ctx.guild.id)
            if thread is not None and auto_delete_thread:
                self._schedule_thread_cleanup(ctx.guild.id, thread, cleanup_last_message_id)

    async def _ensure_global_bind(self) -> None:
        if hasattr(self.bot, "ensure_config_bound"):
            await self.bot.ensure_config_bound()
        bind_event = getattr(self.bot, "config_bind_ready", None)
        if bind_event is not None:
            await bind_event.wait()

    async def _send_formatted(self, channel: discord.abc.Messageable, output: str, prompt: str) -> None:
        for block in self._format_output_blocks(output, prompt):
            await channel.send(block)

    async def _safe_insert_system_log(
        self,
        *,
        actor_user_id: int | None,
        scope_id: int,
        feature: str,
        severity: str,
        message: str,
        detail_json: dict[str, object],
    ) -> None:
        await self.storage.insert_system_log_safe(
            actor_user_id=actor_user_id,
            scope_id=int(scope_id),
            feature=feature,
            severity=severity,
            message=message,
            detail_json=detail_json,
        )

    async def _add_completion_reaction(self, message: object, success: bool, guild_id: int) -> None:
        reactor = getattr(message, "add_reaction", None)
        if reactor is None:
            return
        emoji = "✅" if success else "❌"
        try:
            await reactor(emoji)
        except Exception as exc:
            await self._safe_insert_system_log(
                actor_user_id=None,
                scope_id=int(guild_id),
                feature="cli",
                severity="warn",
                message="reaction-add-failed",
                detail_json={"guild_id": int(guild_id), "error_type": exc.__class__.__name__},
            )
            return

    async def _load_console_config(self, guild_id: int) -> dict:
        row = await self.storage.load_config("guild", guild_id, "console")
        payload = self._running_payload(row.data if row else {})
        if not isinstance(payload, dict):
            payload = {}
        return {
            "always_print_help": bool(payload.get("always_print_help", False)),
            "console_mode": str(payload.get("console_mode", "thread") or "thread"),
            "thread_console_after_delete": bool(payload.get("thread_console_after_delete", False)),
        }

    def _schedule_thread_cleanup(self, guild_id: int, thread: object, last_message_id: int | None) -> None:
        async def cleanup() -> None:
            try:
                await asyncio.sleep(THREAD_DELETE_DELAY_SEC)
                active = self.sessions.get(guild_id)
                if active is not None and active.thread_id == thread.id:
                    return
                if last_message_id is not None and getattr(thread, "last_message_id", None) not in {None, last_message_id}:
                    return
                deleter = getattr(thread, "delete", None)
                if deleter is None:
                    return
                try:
                    await deleter()
                except discord.HTTPException:
                    return
            finally:
                self._cleanup_tasks.discard(task)

        task = asyncio.create_task(cleanup())
        self._cleanup_tasks.add(task)

    def _format_output_blocks(self, output: str, prompt: str) -> list[str]:
        max_body_len = 2000 - len("```text\n") - len("\n```")
        raw_payload = output.strip()
        if not raw_payload:
            return [self._wrap_code_block(self._sanitize_code_block_body(prompt))]

        payload = self._sanitize_code_block_body(raw_payload)
        prompt_suffix = f"\n{self._sanitize_code_block_body(prompt)}"
        if len(payload) + len(prompt_suffix) <= max_body_len:
            return [self._wrap_code_block(f"{payload}{prompt_suffix}")]

        chunks = self._chunk_sanitized_text(raw_payload, max_body_len)
        if len(chunks[-1]) + len(prompt_suffix) <= max_body_len:
            chunks[-1] = f"{chunks[-1]}{prompt_suffix}"
            return [self._wrap_code_block(chunk) for chunk in chunks]

        blocks = [self._wrap_code_block(chunk) for chunk in chunks]
        blocks.append(self._wrap_code_block(prompt))
        return blocks

    def _chunk_text(self, text: str, max_len: int) -> list[str]:
        if max_len <= 0:
            return [text]
        if not text:
            return [""]

        chunks: list[str] = []
        current = ""
        for line in text.split("\n"):
            token = line if current == "" else f"\n{line}"
            if len(token) > max_len:
                if current:
                    chunks.append(current)
                    current = ""
                for piece in self._split_long_token(token.lstrip("\n"), max_len):
                    if not chunks:
                        chunks.append(piece)
                    else:
                        chunks.append(piece)
                continue

            if len(current) + len(token) > max_len:
                chunks.append(current)
                current = line
            else:
                current += token

        if current:
            chunks.append(current)
        return chunks if chunks else [text]

    def _split_long_token(self, token: str, max_len: int) -> list[str]:
        if len(token) <= max_len:
            return [token]
        return [token[index : index + max_len] for index in range(0, len(token), max_len)]

    def _sanitize_code_block_body(self, body: str) -> str:
        return body.replace("```", "`\u200b`\u200b`")

    def _chunk_sanitized_text(self, text: str, max_len: int) -> list[str]:
        if max_len <= 0:
            return [self._sanitize_code_block_body(text)]
        if not text:
            return [""]

        chunks: list[str] = []
        current = ""
        index = 0
        while index < len(text):
            if text.startswith("```", index):
                token = "`\u200b`\u200b`"
                index += 3
            else:
                token = text[index]
                index += 1

            if current and len(current) + len(token) > max_len:
                chunks.append(current)
                current = ""
            current += token

        if current:
            chunks.append(current)
        return chunks if chunks else [""]

    def _wrap_code_block(self, body: str) -> str:
        return f"```text\n{body}\n```"

    def _split_input_lines(self, content: str) -> list[str]:
        lines = [line.strip() for line in content.splitlines()]
        return [line for line in lines if line]

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

    async def _validate_cli_set(self, ctx: EngineContext, session, section_key: str, key: str, values: list[str]) -> str | None:
        if section_key != "auto-reaction" or key != "emojis":
            return None
        guild = self.bot.get_guild(session.scope_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(session.scope_id)
            except discord.HTTPException:
                return "field=guild reason=not found hint=bot cannot access guild"
        for raw in values:
            token = raw.strip().strip(",")
            if token == "":
                continue
            match = _CUSTOM_EMOJI_RE.match(token)
            if not match:
                continue
            emoji_id = int(match.group(1))
            emoji = guild.get_emoji(emoji_id)
            if emoji is None:
                return f"field=emojis reason=emoji unavailable hint=bot cannot use custom emoji id={emoji_id}"
        return None

    async def _execute_utils(self, ctx: EngineContext, session, args: list[str]) -> str:
        if not args:
            return "field=execute reason=invalid args hint=execute utils ... | execute console ... | execute cli to-file ... | execute config ... | execute system ... | execute chat-group ..."
        namespace = args[0]
        if namespace == "utils":
            command = args[1] if len(args) > 1 else ""
            if command == "create-webhook":
                return await self._execute_create_webhook(ctx, session, args[2:])
            if command == "delete-webhook":
                return await self._execute_delete_webhook(ctx, session, args[2:])
            return "field=execute reason=invalid args hint=execute utils create-webhook|delete-webhook ..."
        if namespace == "console":
            return await self._execute_console(ctx, session, args[1:])
        if namespace == "cli":
            return await self._execute_cli_log(session, args[1:])
        if namespace == "config":
            return await self._execute_config(ctx, session, args[1:])
        if namespace == "system":
            return await self._execute_system(ctx, session, args[1:])
        if namespace == "chat-group":
            return await self._execute_chat_group(ctx, session, args[1:])
        return "field=execute reason=invalid args hint=execute utils ... | execute console ... | execute cli to-file ... | execute config ... | execute system ... | execute chat-group ..."

    async def _execute_config(self, ctx: EngineContext, session, args: list[str]) -> str:
        if session.scope_type.value != "root":
            return "field=execute reason=permission denied hint=root scope required"
        if not args:
            return "field=execute reason=invalid args hint=execute config rebind root-diff|full all-guilds|guild <guild-id> | execute config deploy all-guilds|guild <guild-id>"

        if len(args) >= 2 and args[0] == "rebind":
            mode = args[1]
            if mode not in {"root-diff", "full"}:
                return "field=execute reason=invalid args hint=execute config rebind root-diff|full all-guilds|guild <guild-id>"
            guild_ids, error = self._resolve_execute_target_guilds(args[2:])
            if error:
                return error
            result = await rebind_many_guilds(self.storage, guild_ids, mode)
            return "\n".join(
                [
                    f"ok total={result.total} success={result.success} failed={result.failed} mode={mode}",
                    *result.details,
                ]
            )

        if len(args) >= 1 and args[0] == "deploy":
            guild_ids, error = self._resolve_execute_target_guilds(args[1:])
            if error:
                return error
            result = await deploy_many_guilds(self.storage, guild_ids)
            return "\n".join(
                [
                    f"ok total={result.total} success={result.success} failed={result.failed} mode=deploy",
                    *result.details,
                ]
            )

        return "field=execute reason=invalid args hint=execute config rebind root-diff|full all-guilds|guild <guild-id> | execute config deploy all-guilds|guild <guild-id>"

    async def _execute_system(self, ctx: EngineContext, session, args: list[str]) -> str:
        if session.scope_type.value != "root":
            return "field=execute reason=permission denied hint=root scope required"
        if args not in (["restart"], ["restart", "keep-active-cli"]):
            return "field=execute reason=invalid args hint=execute system restart [keep-active-cli]"
        if getattr(self.bot, "system_reloading", False):
            return "field=system reason=already reloading hint=retry later"

        keep_active_cli = args == ["restart", "keep-active-cli"]
        setattr(self.bot, "system_reloading", True)
        setattr(self.bot, "system_reloading_keep_active_cli", keep_active_cli)
        try:
            await self._safe_insert_system_log(
                actor_user_id=ctx.actor_user_id,
                scope_id=0,
                feature="system-restart",
                severity="info",
                message="restart-started",
                detail_json={"mode": "keep-active-cli" if keep_active_cli else "full"},
            )
            if hasattr(self.bot, "restart_runtime"):
                await self.bot.restart_runtime(keep_active_cli=keep_active_cli)
            elif hasattr(self.bot, "rebind_all_configs"):
                await self.bot.rebind_all_configs()
            await self._safe_insert_system_log(
                actor_user_id=ctx.actor_user_id,
                scope_id=0,
                feature="system-restart",
                severity="info",
                message="restart-completed",
                detail_json={"mode": "keep-active-cli" if keep_active_cli else "full"},
            )
        finally:
            setattr(self.bot, "system_reloading", False)
            setattr(self.bot, "system_reloading_keep_active_cli", False)
        if keep_active_cli:
            return "ok system restart completed (keep-active-cli)"
        return "ok system restart completed"

    async def _execute_chat_group(self, ctx: EngineContext, session, args: list[str]) -> str:
        if not args:
            return "field=execute reason=invalid args hint=execute chat-group create|join|leave|manage-group|message|guild-setting|global ..."

        cmd = args[0]
        if cmd == "create":
            if session.scope_type.value != "guild":
                return "field=execute reason=permission denied hint=guild scope required"
            parsed = self._parse_chat_group_create_args(args[1:])
            if isinstance(parsed, str):
                return parsed
            name, mode, channel_id = parsed
            channel, error = await self._resolve_channel_for_guild(session.scope_id, channel_id)
            if error:
                return error
            if self._channel_is_nsfw(channel):
                return "field=channel reason=nsfw not allowed hint=use non-nsfw channel"
            group_id = await self.storage.create_chat_group(
                name=name,
                mode=mode,
                leader_guild_id=session.scope_id,
                channel_id=channel_id,
            )
            await self._sync_chat_group_config_for_guilds([session.scope_id])
            return f"ok group-id={group_id}"

        if cmd == "join":
            if session.scope_type.value != "guild":
                return "field=execute reason=permission denied hint=guild scope required"
            parsed_join = self._parse_chat_group_join_args(args[1:])
            if isinstance(parsed_join, str):
                return parsed_join
            group_id, channel_id, auth_key = parsed_join
            group = await self.storage.get_chat_group(group_id)
            if group is None:
                return "field=group-id reason=not found hint=use get chat-group list"
            channel, error = await self._resolve_channel_for_guild(session.scope_id, channel_id)
            if error:
                return error
            if self._channel_is_nsfw(channel):
                return "field=channel reason=nsfw not allowed hint=use non-nsfw channel"
            if auth_key is not None:
                valid = await self.storage.resolve_chat_group_auth_key(group_id, auth_key, session.scope_id)
                if not valid:
                    return "field=auth-key reason=invalid key hint=request valid auth-key"
                await self.storage.upsert_chat_group_membership(
                    group_id=group_id,
                    guild_id=session.scope_id,
                    status="active",
                    role="normal",
                )
                await self.storage.upsert_chat_group_connection(
                    group_id=group_id,
                    guild_id=session.scope_id,
                    channel_id=channel_id,
                    webhook_ref=None,
                )
                await self._sync_chat_group_config_for_group(group_id)
                return f"ok joined group-id={group_id} mode=auth-key"
            if group.mode == "private" or group.join_need_apply:
                apply_id = await self.storage.create_chat_group_application(group_id, session.scope_id, channel_id)
                return f"ok apply-id={apply_id} status=pending"
            await self.storage.upsert_chat_group_membership(
                group_id=group_id,
                guild_id=session.scope_id,
                status="active",
                role="normal",
            )
            await self.storage.upsert_chat_group_connection(
                group_id=group_id,
                guild_id=session.scope_id,
                channel_id=channel_id,
                webhook_ref=None,
            )
            await self._sync_chat_group_config_for_group(group_id)
            return f"ok joined group-id={group_id}"

        if cmd == "leave":
            if session.scope_type.value != "guild":
                return "field=execute reason=permission denied hint=guild scope required"
            if len(args) != 2:
                return "field=execute reason=invalid args hint=execute chat-group leave <group-id>"
            group_id = args[1]
            membership = await self.storage.get_chat_group_membership(group_id, session.scope_id)
            if membership is None:
                return "field=group-id reason=not joined hint=join group first"
            if membership.role == "leader":
                return "field=group-id reason=leader cannot leave hint=transfer leader first"
            await self.storage.upsert_chat_group_membership(
                group_id=group_id,
                guild_id=session.scope_id,
                status="disable",
                role=membership.role,
            )
            await self._sync_chat_group_config_for_group(group_id)
            return f"ok left group-id={group_id}"

        if cmd == "manage-group":
            return await self._execute_chat_group_manage(ctx, session, args[1:])

        if cmd == "message":
            return await self._execute_chat_group_message(ctx, session, args[1:])

        if cmd == "guild-setting":
            if session.scope_type.value != "guild":
                return "field=execute reason=permission denied hint=guild scope required"
            if len(args) != 3 or args[1] not in {"ban", "unban"}:
                return "field=execute reason=invalid args hint=execute chat-group guild-setting ban|unban <user-id>"
            try:
                user_id = int(args[2])
            except ValueError:
                return "field=user-id reason=invalid integer hint=use numeric user id"
            await self.storage.set_chat_group_ban(
                group_id=None,
                guild_id=session.scope_id,
                user_id=user_id,
                mode="ban" if args[1] == "ban" else "unban",
                global_scope=False,
            )
            return f"ok guild-setting {args[1]} user={user_id}"

        if cmd == "global":
            if session.scope_type.value != "root":
                return "field=execute reason=permission denied hint=root scope required"
            if len(args) != 3 or args[1] not in {"ban", "unban"}:
                return "field=execute reason=invalid args hint=execute chat-group global ban|unban <user-id>"
            try:
                user_id = int(args[2])
            except ValueError:
                return "field=user-id reason=invalid integer hint=use numeric user id"
            await self.storage.set_chat_group_ban(
                group_id=None,
                guild_id=None,
                user_id=user_id,
                mode="ban" if args[1] == "ban" else "unban",
                global_scope=True,
            )
            return f"ok global {args[1]} user={user_id}"

        return "field=execute reason=invalid args hint=execute chat-group create|join|leave|manage-group|message|guild-setting|global ..."

    async def _execute_chat_group_manage(self, ctx: EngineContext, session, args: list[str]) -> str:
        if len(args) < 2:
            return "field=execute reason=invalid args hint=execute chat-group manage-group <group-id> ..."
        group_id = args[0]
        action = args[1]
        group = await self.storage.get_chat_group(group_id)
        if group is None:
            return "field=group-id reason=not found hint=use get chat-group list"
        if not await self._has_chat_group_manage_permission(session, group_id):
            return "field=execute reason=permission denied hint=leader/manager/root required"

        if action in {"approve", "deny"}:
            if len(args) != 3:
                return "field=execute reason=invalid args hint=execute chat-group manage-group <group-id> approve|deny <apply-id>"
            apply_id = args[2]
            all_rows = await self.storage.list_chat_group_applications(group_id)
            if not any(row.apply_id == apply_id for row in all_rows):
                return "field=apply-id reason=not found hint=use get chat-group <group-id> apply-list"
            pending_rows = await self.storage.list_chat_group_applications(group_id, status="pending")
            if not any(row.apply_id == apply_id for row in pending_rows):
                return "field=apply-id reason=invalid state hint=pending only"
            status = "approved" if action == "approve" else "denied"
            decided = await self.storage.decide_chat_group_application(apply_id, status, decided_by=session.actor_user_id)
            if decided is None:
                return "field=apply-id reason=invalid state hint=pending only"
            if status == "approved":
                await self.storage.upsert_chat_group_membership(
                    group_id=group_id,
                    guild_id=decided.guild_id,
                    status="active",
                    role="normal",
                )
                await self.storage.upsert_chat_group_connection(
                    group_id=group_id,
                    guild_id=decided.guild_id,
                    channel_id=decided.channel_id,
                    webhook_ref=None,
                )
            await self._sync_chat_group_config_for_group(group_id)
            return f"ok {action} apply-id={apply_id}"

        if action == "auth-key":
            if len(args) >= 3 and args[2] == "create":
                guild_scope: int | None = None
                if len(args) == 5 and args[3] == "guild":
                    try:
                        guild_scope = int(args[4])
                    except ValueError:
                        return "field=guild-id reason=invalid integer hint=use numeric guild id"
                elif len(args) != 3:
                    return "field=execute reason=invalid args hint=execute chat-group manage-group <group-id> auth-key create [guild <guild-id>]"
                key_id, plain = await self.storage.create_chat_group_auth_key(group_id, guild_id=guild_scope)
                return f"ok auth-key-id={key_id} auth-key={plain}"
            if len(args) == 5 and args[2] == "revoke" and args[3] == "id":
                try:
                    key_id = int(args[4])
                except ValueError:
                    return "field=id reason=invalid integer hint=use numeric id"
                changed = await self.storage.revoke_chat_group_auth_key(group_id, key_id)
                if not changed:
                    return "field=id reason=not found hint=use get chat-group <group-id> auth-key list"
                return f"ok auth-key revoked id={key_id}"
            return "field=execute reason=invalid args hint=execute chat-group manage-group <group-id> auth-key create [guild <guild-id>] | auth-key revoke id <id>"

        if action == "set-role":
            if len(args) != 5 or args[3] != "guild" or args[2] not in {"manager", "normal"}:
                return "field=execute reason=invalid args hint=execute chat-group manage-group <group-id> set-role manager|normal guild <guild-id>"
            try:
                guild_id = int(args[4])
            except ValueError:
                return "field=guild-id reason=invalid integer hint=use numeric guild id"
            await self.storage.set_chat_group_role(group_id, guild_id, args[2])
            await self._sync_chat_group_config_for_group(group_id)
            return f"ok role updated guild={guild_id} role={args[2]}"

        if action == "transfer-leader":
            if len(args) != 3:
                return "field=execute reason=invalid args hint=execute chat-group manage-group <group-id> transfer-leader <guild-id>"
            if not await self._has_chat_group_leader_permission(session, group_id):
                return "field=execute reason=permission denied hint=leader/root required"
            try:
                guild_id = int(args[2])
            except ValueError:
                return "field=guild-id reason=invalid integer hint=use numeric guild id"
            await self.storage.transfer_chat_group_leader(group_id, guild_id)
            await self._sync_chat_group_config_for_group(group_id)
            return f"ok leader transferred guild={guild_id}"

        if action in {"ban", "unban"}:
            if len(args) != 3:
                return "field=execute reason=invalid args hint=execute chat-group manage-group <group-id> ban|unban <user-id>"
            try:
                user_id = int(args[2])
            except ValueError:
                return "field=user-id reason=invalid integer hint=use numeric user id"
            await self.storage.set_chat_group_ban(
                group_id=group_id,
                guild_id=None,
                user_id=user_id,
                mode="ban" if action == "ban" else "unban",
                global_scope=False,
            )
            return f"ok group {action} user={user_id}"

        return "field=execute reason=invalid args hint=execute chat-group manage-group <group-id> approve|deny|auth-key|set-role|transfer-leader|ban|unban ..."

    async def _execute_chat_group_message(self, ctx: EngineContext, session, args: list[str]) -> str:
        if len(args) != 3 or args[1] != "delete":
            return "field=execute reason=invalid args hint=execute chat-group message <group-id> delete <message-id>"
        group_id = args[0]
        if not await self._has_chat_group_manage_permission(session, group_id):
            return "field=execute reason=permission denied hint=leader/manager/root required"
        try:
            message_id = int(args[2])
        except ValueError:
            return "field=message-id reason=invalid integer hint=use numeric id"
        row = await self.storage.get_chat_group_message(message_id)
        if row is None or row.group_id != group_id:
            return "field=message-id reason=not found hint=check group/message id"
        deliveries = await self.storage.list_chat_group_deliveries(message_id)
        for item in deliveries:
            target_channel = self.bot.get_channel(int(item["target_channel_id"]))
            if target_channel is None:
                await self._safe_insert_system_log(
                    actor_user_id=session.actor_user_id,
                    scope_id=int(session.scope_id),
                    feature="chat-group",
                    severity="warn",
                    message="message-delete-failed",
                    detail_json={
                        "group_id": group_id,
                        "message_id": int(message_id),
                        "target_channel_id": int(item["target_channel_id"]),
                        "target_message_id": int(item.get("target_message_id")) if item.get("target_message_id") is not None else None,
                        "error_type": "channel-unavailable",
                        "reason": "target channel not found",
                    },
                )
                continue
            target_message_id = item.get("target_message_id")
            if target_message_id is None:
                continue
            fetcher = getattr(target_channel, "fetch_message", None)
            if not callable(fetcher):
                await self._safe_insert_system_log(
                    actor_user_id=session.actor_user_id,
                    scope_id=int(session.scope_id),
                    feature="chat-group",
                    severity="warn",
                    message="message-delete-failed",
                    detail_json={
                        "group_id": group_id,
                        "message_id": int(message_id),
                        "target_channel_id": int(item["target_channel_id"]),
                        "target_message_id": int(target_message_id),
                        "error_type": "fetch-unsupported",
                        "reason": "target channel has no fetch_message",
                    },
                )
                continue
            try:
                target_message = await fetcher(int(target_message_id))
                await target_message.delete()
            except discord.NotFound as exc:
                await self._safe_insert_system_log(
                    actor_user_id=session.actor_user_id,
                    scope_id=int(session.scope_id),
                    feature="chat-group",
                    severity="warn",
                    message="message-delete-failed",
                    detail_json={
                        "group_id": group_id,
                        "message_id": int(message_id),
                        "target_channel_id": int(item["target_channel_id"]),
                        "target_message_id": int(target_message_id),
                        "error_type": exc.__class__.__name__,
                    },
                )
                continue
            except discord.Forbidden as exc:
                await self._safe_insert_system_log(
                    actor_user_id=session.actor_user_id,
                    scope_id=int(session.scope_id),
                    feature="chat-group",
                    severity="warn",
                    message="message-delete-failed",
                    detail_json={
                        "group_id": group_id,
                        "message_id": int(message_id),
                        "target_channel_id": int(item["target_channel_id"]),
                        "target_message_id": int(target_message_id),
                        "error_type": exc.__class__.__name__,
                    },
                )
                continue
            except discord.HTTPException as exc:
                await self._safe_insert_system_log(
                    actor_user_id=session.actor_user_id,
                    scope_id=int(session.scope_id),
                    feature="chat-group",
                    severity="warn",
                    message="message-delete-failed",
                    detail_json={
                        "group_id": group_id,
                        "message_id": int(message_id),
                        "target_channel_id": int(item["target_channel_id"]),
                        "target_message_id": int(target_message_id),
                        "error_type": exc.__class__.__name__,
                    },
                )
                continue
            except Exception as exc:
                await self._safe_insert_system_log(
                    actor_user_id=session.actor_user_id,
                    scope_id=int(session.scope_id),
                    feature="chat-group",
                    severity="error",
                    message="message-delete-failed",
                    detail_json={
                        "group_id": group_id,
                        "message_id": int(message_id),
                        "target_channel_id": int(item["target_channel_id"]),
                        "target_message_id": int(target_message_id),
                        "error_type": exc.__class__.__name__,
                    },
                )
                continue
        await self.storage.mark_chat_group_message_deleted(message_id)
        return f"ok deleted message-id={message_id}"

    async def _has_chat_group_manage_permission(self, session, group_id: str) -> bool:
        if session.scope_type.value == "root":
            return True
        membership = await self.storage.get_chat_group_membership(group_id, session.scope_id)
        if membership is None:
            return False
        return membership.role in {"leader", "manager"}

    async def _has_chat_group_leader_permission(self, session, group_id: str) -> bool:
        if session.scope_type.value == "root":
            return True
        membership = await self.storage.get_chat_group_membership(group_id, session.scope_id)
        if membership is None:
            return False
        return membership.role == "leader"

    def _parse_chat_group_create_args(self, args: list[str]) -> tuple[str, str, int] | str:
        if len(args) != 6:
            return "field=execute reason=invalid args hint=execute chat-group create name \"<name>\" mode discovery|public|private channel <channel-id>"
        if args[0] != "name" or args[2] != "mode" or args[4] != "channel":
            return "field=execute reason=invalid args hint=execute chat-group create name \"<name>\" mode discovery|public|private channel <channel-id>"
        mode = args[3].lower()
        if mode not in {"discovery", "public", "private"}:
            return "field=mode reason=invalid value hint=discovery|public|private"
        try:
            channel_id = int(args[5])
        except ValueError:
            return "field=channel-id reason=invalid integer hint=use numeric channel id"
        return args[1], mode, channel_id

    def _parse_chat_group_join_args(self, args: list[str]) -> tuple[str, int, str | None] | str:
        if len(args) not in {3, 5}:
            return "field=execute reason=invalid args hint=execute chat-group join <group-id> channel <channel-id> [auth-key <key>]"
        group_id = args[0]
        if args[1] != "channel":
            return "field=execute reason=invalid args hint=execute chat-group join <group-id> channel <channel-id> [auth-key <key>]"
        try:
            channel_id = int(args[2])
        except ValueError:
            return "field=channel-id reason=invalid integer hint=use numeric channel id"
        if len(args) == 5:
            if args[3] != "auth-key":
                return "field=execute reason=invalid args hint=execute chat-group join <group-id> channel <channel-id> [auth-key <key>]"
            return group_id, channel_id, args[4]
        return group_id, channel_id, None

    async def _resolve_channel_for_guild(self, guild_id: int, channel_id: int) -> tuple[object | None, str | None]:
        guild = self.bot.get_guild(int(guild_id))
        if guild is None:
            return None, "field=guild reason=not found hint=bot cannot access guild"
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            fetcher = getattr(guild, "fetch_channel", None)
            if callable(fetcher):
                try:
                    channel = await fetcher(int(channel_id))
                except Exception as exc:
                    await self._safe_insert_system_log(
                        actor_user_id=None,
                        scope_id=int(guild_id),
                        feature="chat-group",
                        severity="warn",
                        message="channel-resolve-failed",
                        detail_json={
                            "guild_id": int(guild_id),
                            "channel_id": int(channel_id),
                            "error_type": exc.__class__.__name__,
                        },
                    )
                    channel = None
        if channel is None:
            return None, "field=channel reason=not found hint=use valid channel id"
        return channel, None

    def _channel_is_nsfw(self, channel: object | None) -> bool:
        if channel is None:
            return False
        checker = getattr(channel, "is_nsfw", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return True
        return bool(getattr(channel, "nsfw", False))

    async def _sync_chat_group_config_for_group(self, group_id: str) -> None:
        memberships = await self.storage.list_chat_group_memberships(group_id)
        guild_ids = sorted({int(row.guild_id) for row in memberships})
        await self._sync_chat_group_config_for_guilds(guild_ids)

    async def _sync_chat_group_config_for_guilds(self, guild_ids: list[int]) -> None:
        for guild_id in guild_ids:
            try:
                groups_snapshot = sorted(await self.storage.list_chat_groups_for_guild(guild_id), key=lambda row: row.group_id)
                group_ids_snapshot = [row.group_id for row in groups_snapshot]
                cli_index = await self.storage.resolve_chat_group_cli_index(int(guild_id), group_ids_snapshot)

                payload_rows: list[dict[str, object]] = []
                for group in groups_snapshot:
                    row_id = int(cli_index.get(group.group_id, 0))
                    if row_id <= 0:
                        continue
                    memberships = await self.storage.list_chat_group_memberships(group.group_id)
                    connections = await self.storage.list_chat_group_connections(group.group_id)
                    connection = next((row for row in connections if int(row.guild_id) == int(guild_id)), None)
                    member_rows = sorted(memberships, key=lambda row: row.guild_id)
                    payload_rows.append(
                        {
                            "id": row_id,
                            "name": group.name,
                            "group_id": group.group_id,
                            "mode": group.mode,
                            "join_need_apply": bool(group.join_need_apply),
                            "status": group.status,
                            "connection": {
                                "channel": int(connection.channel_id) if connection is not None else None,
                                "webhook": connection.webhook_ref if connection is not None else None,
                                "name_format": connection.name_format if connection is not None else "{nickname} / {guild_name}",
                            },
                            "member_guilds": [
                                {
                                    "id": member_index,
                                    "guild": int(member.guild_id),
                                    "status": member.status,
                                    "role": member.role,
                                }
                                for member_index, member in enumerate(member_rows, start=1)
                            ],
                        }
                    )
                payload_rows.sort(key=lambda row: int(row.get("id", 0)))
                envelope = {
                    "schema_version": 1,
                    "payload": {
                        "running_payload": {"groups": payload_rows},
                        "startup_payload": {"groups": payload_rows},
                    },
                }
                await self.storage.upsert_config("guild", int(guild_id), "chat-group", envelope)
            except Exception as exc:
                await self._safe_insert_system_log(
                    actor_user_id=None,
                    scope_id=int(guild_id),
                    feature="chat-group",
                    severity="error",
                    message="sync-config-failed",
                    detail_json={
                        "guild_id": int(guild_id),
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                    },
                )
                continue

    def _resolve_execute_target_guilds(self, args: list[str]) -> tuple[list[int], str | None]:
        if args == ["all-guilds"]:
            ids = sorted({int(g.id) for g in self.bot.guilds if getattr(g, "id", None) is not None})
            return ids, None
        if len(args) == 2 and args[0] == "guild":
            try:
                guild_id = int(args[1])
            except ValueError:
                return [], "field=guild-id reason=invalid integer hint=use numeric guild id"
            if self.bot.get_guild(guild_id) is None:
                return [], "field=guild-id reason=not found hint=bot is not in target guild"
            return [guild_id], None
        return [], "field=execute reason=invalid args hint=... all-guilds|guild <guild-id>"

    async def _execute_cli_log(self, session, args: list[str]) -> str:
        if len(args) in {2, 3} and args[0] == "to-file" and args[1] == "start":
            if session.cli_log_stream_enabled:
                return "field=cli-log reason=already started hint=execute cli to-file stop"
            no_message_response = len(args) == 3 and args[2] == "no-message-response"
            if len(args) == 3 and not no_message_response:
                return "field=execute reason=invalid args hint=execute cli to-file start [no-message-response]|stop"
            stream = io.StringIO()
            started_at = datetime.now(timezone.utc).isoformat()
            stream.write("# stella cli session log\n")
            stream.write(
                "# session_id={0} guild={1} actor={2} started_at={3}\n".format(
                    session.session_id,
                    session.guild_id,
                    session.actor_user_id,
                    started_at,
                )
            )
            self._cli_log_streams[session.session_id] = stream
            session.cli_log_stream_enabled = True
            session.cli_log_no_message_response = no_message_response
            session.cli_log_started_at = started_at
            return "ok cli log started"

        if len(args) == 2 and args[0] == "to-file" and args[1] == "stop":
            if not session.cli_log_stream_enabled:
                return "field=cli-log reason=not started hint=execute cli to-file start [no-message-response]"
            self._cli_log_stop_requested.add(session.session_id)
            return "ok cli log stop requested"

        return "field=execute reason=invalid args hint=execute cli to-file start [no-message-response]|stop"

    async def _execute_console(self, ctx: EngineContext, session, args: list[str]) -> str:
        if len(args) != 4 or args[0] != "thread" or args[1] != "unused" or args[2] != "remove":
            return "field=execute reason=invalid args hint=execute console thread unused remove <channel-id>"
        try:
            channel_id = int(args[3])
        except ValueError:
            return "field=channel-id reason=invalid integer hint=use numeric channel id"

        guild_id = session.scope_id if session.scope_type.value == "guild" else ctx.guild_id
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(guild_id)
            except discord.HTTPException:
                return "field=guild reason=not found hint=bot cannot access guild"

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return "field=channel reason=not found hint=invalid channel id"

        threads: dict[int, object] = {}
        direct_threads = getattr(channel, "threads", None)
        if isinstance(direct_threads, list):
            for thread in direct_threads:
                thread_id = getattr(thread, "id", None)
                if isinstance(thread_id, int):
                    threads[thread_id] = thread

        archived_getter = getattr(channel, "archived_threads", None)
        if callable(archived_getter):
            for private_mode in (False, True):
                try:
                    async for thread in archived_getter(limit=None, private=private_mode):
                        thread_id = getattr(thread, "id", None)
                        if isinstance(thread_id, int):
                            threads[thread_id] = thread
                except Exception as exc:
                    await self._safe_insert_system_log(
                        actor_user_id=session.actor_user_id,
                        scope_id=int(guild_id),
                        feature="console",
                        severity="warn",
                        message="thread-scan-failed",
                        detail_json={
                            "guild_id": int(guild_id),
                            "channel_id": int(channel_id),
                            "private_mode": bool(private_mode),
                            "error_type": exc.__class__.__name__,
                        },
                    )
                    continue

        active_session = self.sessions.get(guild_id)
        removed: list[str] = []
        skipped: list[str] = []
        for thread in sorted(threads.values(), key=lambda value: value.id):
            if not str(getattr(thread, "name", "")).startswith(CLI_THREAD_PREFIX):
                continue
            if active_session is not None and thread.id == active_session.thread_id:
                skipped.append(f"thread={thread.id} status=active-session")
                continue
            try:
                await thread.delete()
                removed.append(f"thread={thread.id} status=deleted")
            except discord.HTTPException as exc:
                skipped.append(f"thread={thread.id} status=failed({exc.__class__.__name__})")

        if not removed and not skipped:
            return f"console thread cleanup channel={channel_id}: no-targets"
        lines = [f"console thread cleanup channel={channel_id}"]
        lines.extend(removed)
        lines.extend(skipped)
        return "\n".join(lines)

    async def _execute_create_webhook(self, ctx: EngineContext, session, args: list[str]) -> str:
        if session.scope_type.value != "guild":
            return "field=execute reason=forbidden hint=switch guild <guild-id>"
        if len(args) < 3:
            return 'field=execute reason=invalid args hint=create-webhook channel <channel-id> tag "<tag>" | auto-context tag "<tag>"'
        mode = args[0]
        tag = ""

        channel_ids: list[int] = []
        if mode == "channel":
            if len(args) != 4 or args[2] != "tag":
                return 'field=execute reason=invalid args hint=create-webhook channel <channel-id> tag "<tag>"'
            try:
                channel_ids = [int(args[1])]
            except ValueError:
                return "field=channel-id reason=invalid integer hint=use numeric channel id"
            tag = args[3]
        elif mode == "auto-context":
            if len(args) != 3 or args[1] != "tag":
                return 'field=execute reason=invalid args hint=create-webhook auto-context tag "<tag>"'
            tag = args[2]
            sticky = await self.storage.load_config("guild", session.scope_id, "sticky-message")
            payload = self._running_payload(sticky.data if sticky else {})
            items = payload.get("items", []) if isinstance(payload, dict) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                channels = item.get("channels", [])
                if not isinstance(channels, list):
                    continue
                for entry in channels:
                    if isinstance(entry, dict) and isinstance(entry.get("channel_id"), int):
                        channel_ids.append(int(entry["channel_id"]))
            channel_ids = sorted(set(channel_ids))
            if not channel_ids:
                return "field=sticky-message reason=no channels hint=set sticky-message channels first"
        else:
            return 'field=execute reason=invalid args hint=create-webhook channel <channel-id>|auto-context'
        if not tag:
            return "field=tag reason=empty value hint=provide tag name"

        guild = self.bot.get_guild(session.scope_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(session.scope_id)
            except discord.HTTPException:
                return "field=guild reason=not found hint=bot cannot access guild"

        created: list[str] = []
        for channel_id in channel_ids:
            channel = await resolve_guild_channel(guild, channel_id)
            if channel is None:
                created.append(f"channel={channel_id} status=failed(fetch-channel)")
                continue
            creator = getattr(channel, "create_webhook", None)
            if creator is None:
                created.append(f"channel={channel_id} status=failed(unsupported-channel)")
                continue
            try:
                webhook = await creator(name=tag, reason=f"stella utils webhook by {ctx.actor_user_id}")
            except discord.HTTPException as exc:
                created.append(f"channel={channel_id} status=failed({exc.__class__.__name__})")
                continue
            if webhook.token is None:
                created.append(f"channel={channel_id} status=failed(no-token)")
                continue
            ref_id = f"wh-{uuid.uuid4().hex[:12]}"
            await self.storage.insert_utility_webhook(
                ref_id=ref_id,
                guild_id=session.scope_id,
                channel_id=channel_id,
                webhook_id=webhook.id,
                webhook_token=webhook.token,
                tag=tag,
            )
            created.append(f"channel={channel_id} id={ref_id} status=ok")
        return "create-webhook:\n" + ("\n".join(created) if created else "(empty)")

    async def _execute_delete_webhook(self, ctx: EngineContext, session, args: list[str]) -> str:
        if len(args) != 1:
            return "field=execute reason=invalid args hint=delete-webhook <id>"
        ref_id = args[0]
        row = await self.storage.get_utility_webhook(ref_id)
        if row is None:
            return f"field=id reason=not found hint={ref_id}"
        if session.scope_type.value != "root" and row.guild_id != session.scope_id:
            return "field=id reason=forbidden hint=switch guild <guild-id>"

        try:
            webhook = discord.Webhook.partial(row.webhook_id, row.webhook_token, client=self.bot)
            await webhook.delete(reason=f"stella utils delete by {ctx.actor_user_id}")
            status = "ok"
        except discord.HTTPException as exc:
            status = f"failed({exc.__class__.__name__})"
        await self.storage.delete_utility_webhook(ref_id)
        return f"delete-webhook id={ref_id} status={status}"

    def _running_payload(self, raw: dict) -> dict:
        return extract_running_payload(raw)

    def _append_cli_log_entry(self, session, prompt: str, line: str, output: str) -> None:
        stream = self._cli_log_streams.get(session.session_id)
        if stream is None:
            return
        stream.write(f"{prompt} {line}\n")
        if output:
            stream.write(output.rstrip())
            stream.write("\n")
        stream.write(f"{prompt}\n")

    def _finalize_cli_log_to_file(self, session) -> discord.File | None:
        stream = self._cli_log_streams.get(session.session_id)
        if stream is None:
            self._discard_cli_log_stream(session)
            return None
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        filename = f"cli-log-{timestamp}.txt"
        payload = stream.getvalue().encode("utf-8")
        self._discard_cli_log_stream(session)
        buffer = io.BytesIO(payload)
        return discord.File(fp=buffer, filename=filename)

    def _discard_cli_log_stream(self, session) -> None:
        stream = self._cli_log_streams.pop(session.session_id, None)
        if stream is not None:
            stream.close()
        self._cli_log_stop_requested.discard(session.session_id)
        self._cli_log_pending_files.pop(session.session_id, None)
        session.cli_log_stream_enabled = False
        session.cli_log_no_message_response = False
        session.cli_log_started_at = None

    def _is_cli_log_stop_command(self, line: str) -> bool:
        tokens = [token.lower() for token in line.strip().split()]
        return tokens == ["execute", "cli", "to-file", "stop"]

    def _is_success_output(self, output: str) -> bool:
        lowered = output.strip().lower()
        return not lowered.startswith("fatal error:")


async def setup(bot: commands.Bot):
    await bot.add_cog(CliCog(bot))
