from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Any

import discord
from discord.ext import commands

from utils.config_runtime import ensure_bind_ready, extract_running_payload
from utils.discord_helpers import resolve_bot_channel, resolve_guild_channel
from utils.storage import ChatGroupConnectionRow, ChatGroupRow, Storage
from utils.tick import TickMeter

logger = logging.getLogger(__name__)

@dataclass
class _RelayTask:
    message: discord.Message
    group: ChatGroupRow


class ChatGroupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.storage = Storage()
        self.tick_meter = getattr(bot, "tick_meter", TickMeter(self.storage))
        self._group_queues: dict[str, asyncio.Queue[_RelayTask]] = {}
        self._group_workers: dict[str, asyncio.Task[None]] = {}

    async def cog_load(self) -> None:
        await self.storage.init_schema()
        try:
            await self.storage.reset_chat_group_rate_limit_states()
        except Exception as exc:
            logger.exception("chat-group rate limit state reset failed")
            await self.storage.insert_system_log_safe(
                actor_user_id=None,
                scope_id=0,
                feature="chat-group",
                severity="warn",
                message="rate-limit-state-reset-failed",
                detail_json={
                    "phase": "startup-reset",
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                },
            )

    def cog_unload(self) -> None:
        for worker in self._group_workers.values():
            worker.cancel()
        self._group_workers.clear()
        self._group_queues.clear()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._ensure_bind_ready()
        if message.guild is None or message.author.bot:
            return
        guild_id = int(message.guild.id)
        if not await self.tick_meter.start_work(guild_id, "chat_group.message.entry", stoppable=True):
            return

        rows = await self.storage.list_chat_groups_for_guild(guild_id)
        if not rows:
            return

        for group in rows:
            if group.status != "active":
                continue
            memberships = await self.storage.list_chat_group_memberships(group.group_id)
            membership = next((item for item in memberships if item.guild_id == guild_id), None)
            if membership is None or membership.status != "active":
                continue
            connections = await self.storage.list_chat_group_connections(group.group_id)
            source_connection = next((item for item in connections if item.guild_id == guild_id), None)
            if source_connection is None or int(source_connection.channel_id) != int(message.channel.id):
                continue
            if await self.storage.is_chat_group_user_banned(group.group_id, guild_id, int(message.author.id)):
                continue

            queue = self._group_queues.setdefault(group.group_id, asyncio.Queue())
            state = await self.storage.get_chat_group_rate_limit_state(group.group_id)
            pending_total = max(0, int(state.queued_count)) + max(0, int(state.inflight_count))
            if pending_total >= max(1, int(group.rate_limit)):
                if group.overlimit_mode == "drop":
                    try:
                        await message.add_reaction("❌")
                    except discord.HTTPException as exc:
                        await self._write_chat_group_event(
                            guild_id=guild_id,
                            severity="warn",
                            message="reaction-failed",
                            detail={
                                "group_id": group.group_id,
                                "guild_id": guild_id,
                                "channel_id": int(message.channel.id),
                                "message_id": int(message.id),
                                "error_type": exc.__class__.__name__,
                            },
                        )
                    continue
            await queue.put(_RelayTask(message=message, group=group))
            await self._update_rate_limit_state(group.group_id, queued_delta=1, inflight_delta=0)
            await self.tick_meter.consume(guild_id, "chat_group.queue.enqueue", amount=1, stoppable=False)
            self._ensure_worker(group.group_id)

    def _ensure_worker(self, group_id: str) -> None:
        current = self._group_workers.get(group_id)
        if current is not None and not current.done():
            return

        async def run() -> None:
            queue = self._group_queues[group_id]
            while True:
                task = await queue.get()
                await self._update_rate_limit_state(task.group.group_id, queued_delta=-1, inflight_delta=1)
                try:
                    await self._relay_message(task.message, task.group)
                finally:
                    await self._update_rate_limit_state(task.group.group_id, queued_delta=0, inflight_delta=-1)
                    queue.task_done()

        self._group_workers[group_id] = asyncio.create_task(run())

    async def _relay_message(self, message: discord.Message, group: ChatGroupRow) -> None:
        source_guild_id = int(message.guild.id)
        source_channel_id = int(message.channel.id)
        source_message_id = int(message.id)

        connections = await self.storage.list_chat_group_active_connections(group.group_id)
        targets = [row for row in connections if row.guild_id != source_guild_id]
        if not targets:
            return

        await self.tick_meter.consume(source_guild_id, "chat_group.delivery.match", amount=1, stoppable=False)

        source_conn = next((row for row in connections if row.guild_id == source_guild_id), None)
        guild_name = message.guild.name
        author_name = self._format_author_name(message, guild_name, source_conn)

        attachment_urls = await self._relay_attachments(message)
        message_id = await self.storage.insert_chat_group_message(
            group_id=group.group_id,
            source_guild_id=source_guild_id,
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            author_user_id=int(message.author.id),
            author_name=author_name,
            content=message.content or "",
            attachment_urls=attachment_urls,
        )
        await self.tick_meter.consume(source_guild_id, "chat_group.message.persist", amount=1, stoppable=False)

        send_body = f"[{group.name}] {author_name}\n{message.content or ''}".strip()
        if attachment_urls:
            send_body = f"{send_body}\n" + "\n".join(attachment_urls)

        for target in targets:
            sent_message_id: int | None = None
            err_text: str | None = None
            try:
                sent_message = await self._send_to_connection(target, send_body)
                if sent_message is not None:
                    sent_message_id = int(sent_message.id)
                    await self.tick_meter.consume(target.guild_id, "chat_group.delivery.send", amount=1, stoppable=False)
                else:
                    err_text = "send returned none"
                    await self._write_chat_group_event(
                        guild_id=source_guild_id,
                        severity="warn",
                        message="relay-send-failed",
                        detail={
                            "group_id": group.group_id,
                            "guild_id": int(target.guild_id),
                            "channel_id": int(target.channel_id),
                            "message_id": int(message.id),
                            "error_type": "send-unavailable",
                        },
                    )
            except Exception as exc:
                err_text = f"{exc.__class__.__name__}:{exc}"
                logger.exception("chat-group relay send failed")
                await self._write_chat_group_event(
                    guild_id=source_guild_id,
                    severity="error",
                    message="relay-send-failed",
                    detail={
                        "group_id": group.group_id,
                        "guild_id": int(target.guild_id),
                        "channel_id": int(target.channel_id),
                        "message_id": int(message.id),
                        "error_type": exc.__class__.__name__,
                    },
                )
            await self.storage.insert_chat_group_delivery(
                message_id=message_id,
                group_id=group.group_id,
                target_guild_id=target.guild_id,
                target_channel_id=target.channel_id,
                target_message_id=sent_message_id,
                status="ok" if sent_message_id is not None else "failed",
                error=err_text,
            )

    async def _send_to_connection(self, connection: ChatGroupConnectionRow, content: str) -> discord.Message | None:
        guild = self.bot.get_guild(int(connection.guild_id))
        if guild is None:
            return None
        channel = await resolve_guild_channel(guild, int(connection.channel_id))
        if channel is None:
            return None

        kwargs = {
            "content": content,
            "allowed_mentions": discord.AllowedMentions.none(),
        }
        if connection.webhook_ref:
            row = await self.storage.get_utility_webhook(connection.webhook_ref)
            if row is not None and int(row.guild_id) == int(connection.guild_id):
                webhook = discord.Webhook.partial(row.webhook_id, row.webhook_token, client=self.bot)
                try:
                    return await webhook.send(wait=True, **kwargs)
                except discord.HTTPException:
                    return None
        sender = getattr(channel, "send", None)
        if not callable(sender):
            return None
        return await sender(**kwargs)

    async def _relay_attachments(self, message: discord.Message) -> list[str]:
        if not message.attachments:
            return []
        attachment_channel_id = await self._attachment_channel_id_from_root()
        if attachment_channel_id is None:
            await self.storage.insert_system_log_safe(
                actor_user_id=None,
                scope_id=message.guild.id,
                feature="chat-group-attachment",
                severity="warn",
                message="attachment-relay-skipped",
                detail_json={"reason": "attachment channel not configured"},
            )
            return []

        channel = await resolve_bot_channel(self.bot, int(attachment_channel_id))
        if channel is None:
            await self.storage.insert_system_log_safe(
                actor_user_id=None,
                scope_id=message.guild.id,
                feature="chat-group-attachment",
                severity="warn",
                message="attachment-relay-skipped",
                detail_json={"reason": "attachment channel unavailable", "attachment_channel_id": attachment_channel_id},
            )
            return []

        urls: list[str] = []
        for item in message.attachments:
            try:
                blob = await item.read(use_cached=True)
                await self.tick_meter.consume(message.guild.id, "chat_group.attachment.read", amount=1, stoppable=False)
            except discord.HTTPException as exc:
                await self._write_chat_group_event(
                    guild_id=int(message.guild.id),
                    severity="warn",
                    message="attachment-read-failed",
                    detail={
                        "group_id": None,
                        "guild_id": int(message.guild.id),
                        "channel_id": int(message.channel.id),
                        "message_id": int(message.id),
                        "error_type": exc.__class__.__name__,
                    },
                )
                continue
            except Exception as exc:
                logger.exception("chat-group attachment read failed")
                await self._write_chat_group_event(
                    guild_id=int(message.guild.id),
                    severity="error",
                    message="attachment-read-failed",
                    detail={
                        "group_id": None,
                        "guild_id": int(message.guild.id),
                        "channel_id": int(message.channel.id),
                        "message_id": int(message.id),
                        "error_type": exc.__class__.__name__,
                    },
                )
                continue
            stream = io.BytesIO(blob)
            try:
                stream.seek(0)
                file = discord.File(stream, filename=item.filename or f"attachment-{item.id}")
                try:
                    posted = await channel.send(file=file, allowed_mentions=discord.AllowedMentions.none())
                    await self.tick_meter.consume(message.guild.id, "chat_group.attachment.relay", amount=1, stoppable=False)
                except discord.HTTPException as exc:
                    await self._write_chat_group_event(
                        guild_id=int(message.guild.id),
                        severity="warn",
                        message="attachment-relay-failed",
                        detail={
                            "group_id": None,
                            "guild_id": int(message.guild.id),
                            "channel_id": int(message.channel.id),
                            "message_id": int(message.id),
                            "error_type": exc.__class__.__name__,
                        },
                    )
                    continue
                except Exception as exc:
                    logger.exception("chat-group attachment relay failed")
                    await self._write_chat_group_event(
                        guild_id=int(message.guild.id),
                        severity="error",
                        message="attachment-relay-failed",
                        detail={
                            "group_id": None,
                            "guild_id": int(message.guild.id),
                            "channel_id": int(message.channel.id),
                            "message_id": int(message.id),
                            "error_type": exc.__class__.__name__,
                        },
                    )
                    continue
                if posted.attachments:
                    urls.append(posted.attachments[0].url)
            finally:
                stream.close()
        return urls

    async def _attachment_channel_id_from_root(self) -> int | None:
        row = await self.storage.load_config("root", 0, "chat-group-global")
        if row is None or not isinstance(row.data, dict):
            return None
        raw_payload = row.data.get("payload", row.data)
        startup = raw_payload.get("startup_payload") if isinstance(raw_payload, dict) else None
        active = startup if isinstance(startup, dict) else extract_running_payload(row.data)
        if not isinstance(active, dict):
            return None
        value = active.get("attachment_channel_id")
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _format_author_name(self, message: discord.Message, guild_name: str, source_conn: ChatGroupConnectionRow | None) -> str:
        member = message.author if isinstance(message.author, discord.Member) else None
        nickname = member.nick if member and member.nick else message.author.name
        username = message.author.name
        display_user = str(message.author)
        pattern = source_conn.name_format if source_conn is not None else "{nickname} / {guild_name}"
        return (
            pattern.replace("{nickname}", nickname)
            .replace("{username}", username)
            .replace("{user}", display_user)
            .replace("{guild_name}", guild_name)
        )

    async def _ensure_bind_ready(self) -> None:
        await ensure_bind_ready(self.bot)

    async def _update_rate_limit_state(self, group_id: str, *, queued_delta: int, inflight_delta: int) -> None:
        try:
            await self.storage.increment_chat_group_queue(group_id, queued_delta=queued_delta, inflight_delta=inflight_delta)
        except Exception as exc:
            logger.exception("chat-group rate limit state update failed")
            try:
                group = await self.storage.get_chat_group(group_id)
                scope_id = int(group.leader_guild_id) if group is not None else 0
                logged = await self.storage.insert_system_log_safe(
                    actor_user_id=None,
                    scope_id=scope_id,
                    feature="chat-group",
                    severity="error",
                    message="rate-limit-state-update-failed",
                    detail_json={
                        "group_id": group_id,
                        "queued_delta": int(queued_delta),
                        "inflight_delta": int(inflight_delta),
                        "error_type": exc.__class__.__name__,
                    },
                )
                if not logged:
                    await self.storage.insert_system_log_safe(
                        actor_user_id=None,
                        scope_id=0,
                        feature="chat-group",
                        severity="error",
                        message="rate-limit-state-update-failed-fallback",
                        detail_json={
                            "group_id": group_id,
                            "queued_delta": int(queued_delta),
                            "inflight_delta": int(inflight_delta),
                            "original_error_type": exc.__class__.__name__,
                        },
                    )
            except Exception:
                logger.exception("chat-group rate limit failure logging failed")

    async def _write_chat_group_event(self, guild_id: int, severity: str, message: str, detail: dict[str, Any]) -> None:
        await self.storage.insert_system_log_safe(
            actor_user_id=None,
            scope_id=int(guild_id),
            feature="chat-group",
            severity=severity,
            message=message,
            detail_json=detail,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatGroupCog(bot))
