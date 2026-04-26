from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace

import aiosqlite
import pytest

import cogs.chat_group as chat_group_module
from cogs.chat_group import ChatGroupCog


class _FakeGuild:
    def __init__(self, guild_id: int, name: str = "guild") -> None:
        self.id = guild_id
        self.name = name


class _FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class _FakeAuthor:
    def __init__(self, user_id: int, name: str = "tester") -> None:
        self.id = user_id
        self.name = name
        self.bot = False

    def __str__(self) -> str:
        return f"{self.name}#0001"


class _FakeRelayChannel:
    def __init__(self) -> None:
        self.sent_files: list[object] = []

    async def send(self, *args, **kwargs):
        self.sent_files.append((args, kwargs))
        return SimpleNamespace(attachments=[])


class _FailRelayChannel:
    async def send(self, *args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("relay send failed")


class _FakeBot:
    def __init__(self) -> None:
        self._channels: dict[int, object] = {}

    def get_guild(self, guild_id: int):
        return _FakeGuild(guild_id)

    def get_channel(self, channel_id: int):
        return self._channels.get(int(channel_id))

    async def fetch_channel(self, channel_id: int):
        return self._channels.get(int(channel_id))


class _FakeTickMeter:
    async def start_work(self, guild_id: int, source: str, stoppable: bool):
        _ = (guild_id, source, stoppable)
        return True

    async def consume(self, guild_id: int, source: str, amount: int = 1, stoppable: bool = False):
        _ = (guild_id, source, amount, stoppable)
        return True


class _InboundMessage:
    def __init__(self, guild_id: int, channel_id: int, author_id: int = 5001) -> None:
        self.id = 7001
        self.guild = _FakeGuild(guild_id, "source")
        self.channel = _FakeChannel(channel_id)
        self.author = _FakeAuthor(author_id, "alice")
        self.content = "hello"
        self.attachments: list[object] = []
        self.reactions: list[str] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)


@pytest.mark.asyncio
async def test_chat_group_relay_failure_writes_system_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cog = ChatGroupCog(_FakeBot())
    await cog.cog_load()

    group_id = await cog.storage.create_chat_group(
        name="observe",
        mode="public",
        leader_guild_id=1001,
        channel_id=2001,
    )
    await cog.storage.upsert_chat_group_membership(group_id=group_id, guild_id=1002, status="active", role="normal")
    await cog.storage.upsert_chat_group_connection(group_id=group_id, guild_id=1002, channel_id=2002, webhook_ref=None)
    group = await cog.storage.get_chat_group(group_id)
    assert group is not None

    async def _raise_send(*args, **kwargs):
        raise RuntimeError("send boom")

    monkeypatch.setattr(cog, "_send_to_connection", _raise_send)

    message = SimpleNamespace(
        id=3001,
        guild=_FakeGuild(1001, "source"),
        channel=_FakeChannel(2001),
        author=_FakeAuthor(5001, "alice"),
        content="hello",
        attachments=[],
    )
    await cog._relay_message(message, group)

    logs = await cog.storage.fetch_logs("system", 1001, 50)
    assert any(log.section == "chat-group" and log.result == "relay-send-failed" for log in logs)


@pytest.mark.asyncio
async def test_chat_group_attachment_read_failure_writes_system_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = _FakeBot()
    bot._channels[9001] = _FakeRelayChannel()
    cog = ChatGroupCog(bot)
    await cog.cog_load()

    root_payload = {
        "schema_version": 1,
        "payload": {
            "running_payload": {"attachment_channel_id": 9001},
            "startup_payload": {"attachment_channel_id": 9001},
        },
    }
    await cog.storage.upsert_config("root", 0, "chat-group-global", root_payload)

    class _BadAttachment:
        id = 1
        filename = "bad.txt"

        async def read(self, use_cached: bool = True):
            _ = use_cached
            raise RuntimeError("cannot read")

    message = SimpleNamespace(
        id=4001,
        guild=_FakeGuild(1001, "source"),
        channel=_FakeChannel(2001),
        author=_FakeAuthor(5001, "alice"),
        content="hello",
        attachments=[_BadAttachment()],
    )
    urls = await cog._relay_attachments(message)
    assert urls == []

    logs = await cog.storage.fetch_logs("system", 1001, 50)
    assert any(log.section == "chat-group" and log.result == "attachment-read-failed" for log in logs)


@pytest.mark.asyncio
async def test_chat_group_attachment_stream_closed_on_relay_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = _FakeBot()
    bot._channels[9002] = _FailRelayChannel()
    cog = ChatGroupCog(bot)
    await cog.cog_load()

    root_payload = {
        "schema_version": 1,
        "payload": {
            "running_payload": {"attachment_channel_id": 9002},
            "startup_payload": {"attachment_channel_id": 9002},
        },
    }
    await cog.storage.upsert_config("root", 0, "chat-group-global", root_payload)

    class _TrackBytesIO(io.BytesIO):
        closed_count = 0

        def close(self) -> None:
            type(self).closed_count += 1
            super().close()

    class _FakeDiscordFile:
        def __init__(self, stream: io.BytesIO, filename: str) -> None:
            self.stream = stream
            self.filename = filename

    class _GoodAttachment:
        id = 2
        filename = "ok.txt"

        async def read(self, use_cached: bool = True):
            _ = use_cached
            return b"abc"

    monkeypatch.setattr(chat_group_module.io, "BytesIO", _TrackBytesIO)
    monkeypatch.setattr(chat_group_module.discord, "File", _FakeDiscordFile)
    message = SimpleNamespace(
        id=4002,
        guild=_FakeGuild(1001, "source"),
        channel=_FakeChannel(2001),
        author=_FakeAuthor(5001, "alice"),
        content="hello",
        attachments=[_GoodAttachment()],
    )
    urls = await cog._relay_attachments(message)
    assert urls == []
    assert _TrackBytesIO.closed_count == 1


@pytest.mark.asyncio
async def test_chat_group_drop_mode_uses_queued_plus_inflight(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cog = ChatGroupCog(_FakeBot())
    cog.tick_meter = _FakeTickMeter()
    await cog.cog_load()

    group_id = await cog.storage.create_chat_group(
        name="drop-mode",
        mode="public",
        leader_guild_id=1001,
        channel_id=2001,
    )
    async with aiosqlite.connect(cog.storage._sqlite_path) as db:
        await db.execute(
            "UPDATE chat_group_groups SET rate_limit=1, overlimit_mode='drop' WHERE group_id=?",
            (group_id,),
        )
        await db.commit()
    await cog.storage.increment_chat_group_queue(group_id, queued_delta=1, inflight_delta=0)
    message = _InboundMessage(guild_id=1001, channel_id=2001)

    await cog.on_message(message)
    assert "❌" in message.reactions
    state = await cog.storage.get_chat_group_rate_limit_state(group_id)
    assert state.queued_count == 1
    assert state.inflight_count == 0
    cog.cog_unload()


@pytest.mark.asyncio
async def test_chat_group_worker_balances_queue_and_inflight(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cog = ChatGroupCog(_FakeBot())
    cog.tick_meter = _FakeTickMeter()
    await cog.cog_load()

    group_id = await cog.storage.create_chat_group(
        name="queue-mode",
        mode="public",
        leader_guild_id=1001,
        channel_id=2001,
    )
    await cog.storage.upsert_chat_group_membership(group_id=group_id, guild_id=1002, status="active", role="normal")
    await cog.storage.upsert_chat_group_connection(group_id=group_id, guild_id=1002, channel_id=2002, webhook_ref=None)

    group = await cog.storage.get_chat_group(group_id)
    assert group is not None

    async def _slow_relay(message, relay_group):
        _ = (message, relay_group)
        await asyncio.sleep(0.02)

    monkeypatch.setattr(cog, "_relay_message", _slow_relay)
    message = _InboundMessage(guild_id=1001, channel_id=2001)
    await cog.on_message(message)

    for _ in range(30):
        state = await cog.storage.get_chat_group_rate_limit_state(group_id)
        if state.queued_count == 0 and state.inflight_count == 0:
            break
        await asyncio.sleep(0.01)
    state = await cog.storage.get_chat_group_rate_limit_state(group_id)
    assert state.queued_count == 0
    assert state.inflight_count == 0
    cog.cog_unload()


@pytest.mark.asyncio
async def test_chat_group_cog_load_resets_rate_limit_state(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cog = ChatGroupCog(_FakeBot())
    await cog.storage.init_schema()
    await cog.storage.increment_chat_group_queue("cg-reset", queued_delta=5, inflight_delta=4)

    await cog.cog_load()
    state = await cog.storage.get_chat_group_rate_limit_state("cg-reset")
    assert state.queued_count == 0
    assert state.inflight_count == 0


@pytest.mark.asyncio
async def test_chat_group_rate_limit_update_failure_writes_system_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cog = ChatGroupCog(_FakeBot())
    await cog.cog_load()

    group_id = await cog.storage.create_chat_group(
        name="rate-log",
        mode="public",
        leader_guild_id=1001,
        channel_id=2001,
    )

    async def _raise_update(*args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("queue update failed")

    monkeypatch.setattr(cog.storage, "increment_chat_group_queue", _raise_update)
    await cog._update_rate_limit_state(group_id, queued_delta=1, inflight_delta=0)

    logs = await cog.storage.fetch_logs("system", 1001, 30)
    assert any(log.section == "chat-group" and log.result == "rate-limit-state-update-failed" for log in logs)


@pytest.mark.asyncio
async def test_chat_group_cog_load_reset_failure_logs_to_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cog = ChatGroupCog(_FakeBot())
    await cog.storage.init_schema()

    async def _raise_reset(*args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("reset boom")

    monkeypatch.setattr(cog.storage, "reset_chat_group_rate_limit_states", _raise_reset)
    await cog.cog_load()
    logs = await cog.storage.fetch_logs("system", 0, 20)
    assert any(log.section == "chat-group" and log.result == "rate-limit-state-reset-failed" for log in logs)


@pytest.mark.asyncio
async def test_chat_group_rate_limit_update_failure_falls_back_to_root_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cog = ChatGroupCog(_FakeBot())
    await cog.cog_load()

    group_id = await cog.storage.create_chat_group(
        name="rate-log-fallback",
        mode="public",
        leader_guild_id=1001,
        channel_id=2001,
    )

    async def _raise_update(*args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("queue update failed")

    real_insert = cog.storage.insert_system_log
    call_count = {"value": 0}

    async def _flaky_insert(*args, **kwargs):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise RuntimeError("primary log write failed")
        return await real_insert(*args, **kwargs)

    monkeypatch.setattr(cog.storage, "increment_chat_group_queue", _raise_update)
    monkeypatch.setattr(cog.storage, "insert_system_log", _flaky_insert)
    await cog._update_rate_limit_state(group_id, queued_delta=1, inflight_delta=0)

    logs = await cog.storage.fetch_logs("system", 0, 20)
    assert any(log.section == "chat-group" and log.result == "rate-limit-state-update-failed-fallback" for log in logs)
