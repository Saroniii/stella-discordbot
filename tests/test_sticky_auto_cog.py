from __future__ import annotations

from dataclasses import dataclass
import logging
from types import SimpleNamespace

import discord
import pytest

from cogs.sticky_auto import StickyAutoCog


async def _noop_wait():
    return None


class FakeReady:
    async def wait(self):
        await _noop_wait()


@dataclass
class FakeRole:
    id: int


class FakeUser:
    def __init__(self, user_id: int, *, bot: bool = False):
        self.id = user_id
        self.bot = bot
        self.roles: list[FakeRole] = []


class FakePrevMessage:
    def __init__(self, message_id: int):
        self.id = message_id
        self.deleted = False

    async def delete(self):
        self.deleted = True


class FakePrevMessageNotFound(FakePrevMessage):
    async def delete(self):
        raise discord.NotFound(SimpleNamespace(status=404, reason="notfound", text=""), "gone")


class FakePrevMessageHttpError(FakePrevMessage):
    async def delete(self):
        raise discord.HTTPException(SimpleNamespace(status=500, reason="error", text=""), "boom")


class FakeSentMessage:
    def __init__(self, message_id: int):
        self.id = message_id


class FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.sent: list[tuple[str | None, object | None]] = []
        self._next_id = 9000
        self._stored: dict[int, FakePrevMessage] = {}

    async def send(self, content: str | None = None, embed=None):
        self.sent.append((content, embed))
        self._next_id += 1
        message = FakeSentMessage(self._next_id)
        self._stored[message.id] = FakePrevMessage(message.id)
        return message

    async def fetch_message(self, message_id: int):
        return self._stored[message_id]


class FakeChannelNoFetch:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.sent: list[tuple[str | None, object | None]] = []

    async def send(self, content: str | None = None, embed=None):
        self.sent.append((content, embed))
        return FakeSentMessage(9900)


class FakeGuild:
    def __init__(self, guild_id: int, channel: FakeChannel):
        self.id = guild_id
        self._channel = channel
        self._emoji: dict[int, object] = {}

    def get_emoji(self, emoji_id: int):
        return self._emoji.get(emoji_id)

    def add_emoji(self, emoji_id: int, emoji: object):
        self._emoji[emoji_id] = emoji


class FakeMessage:
    def __init__(
        self,
        guild: FakeGuild,
        channel: FakeChannel,
        author: FakeUser,
        content: str,
        *,
        message_id: int = 100,
        webhook_id: int | None = None,
        embeds: list[object] | None = None,
    ):
        self.guild = guild
        self.channel = channel
        self.author = author
        self.content = content
        self.id = message_id
        self.webhook_id = webhook_id
        self.embeds = embeds or []
        self.added_reactions: list[object] = []

    async def add_reaction(self, emoji):
        self.added_reactions.append(emoji)


class FakeTickMeter:
    def __init__(self):
        self.start_calls: list[tuple[int, str, bool]] = []
        self.consume_calls: list[tuple[int, str, int, bool]] = []

    async def start_work(self, guild_id: int, source: str, stoppable: bool):
        self.start_calls.append((guild_id, source, stoppable))
        return True

    async def consume(self, guild_id: int, source: str, amount: int = 1, stoppable: bool = False):
        self.consume_calls.append((guild_id, source, amount, stoppable))
        return True


class FakeBot:
    def __init__(self):
        self.config_bind_ready = FakeReady()
        self.user = FakeUser(999, bot=True)

    async def ensure_config_bound(self):
        return None


def _envelope(payload: dict) -> dict:
    return {"schema_version": 1, "payload": {"running_payload": payload, "startup_payload": payload}}


@pytest.mark.asyncio
async def test_sticky_message_bot_mode_consumes_ticks_and_reposts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello")

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky here",
                        "delay": 0,
                        "trigger_bot_message": False,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    await cog.on_message(message)
    await cog.on_message(message)

    assert len(guild_channel.sent) == 2
    assert guild_channel.sent[0][0] == "sticky here"
    assert any(source == "sticky.message.match" for _, source, _ in cog.tick_meter.start_calls)
    assert any(source == "sticky.message.apply" for _, source, _, _ in cog.tick_meter.consume_calls)


@pytest.mark.asyncio
async def test_sticky_message_webhook_mode_uses_utility_table(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello")

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.insert_utility_webhook(
        ref_id="wh-1",
        guild_id=1234,
        channel_id=777,
        webhook_id=5000,
        webhook_token="token-x",
        tag="sticky",
    )
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky via webhook",
                        "delay": 0,
                        "trigger_bot_message": False,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "webhook", "webhook": {"name": "sticky-bot", "icon": None, "webhook": "wh-1"}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    sent_payload: list[dict] = []

    class FakeWebhook:
        async def send(self, **kwargs):
            sent_payload.append(kwargs)
            return FakeSentMessage(11111)

    monkeypatch.setattr("cogs.sticky_auto.discord.Webhook.partial", lambda *args, **kwargs: FakeWebhook())

    await cog.on_message(message)
    assert len(sent_payload) == 1
    assert sent_payload[0]["content"] == "sticky via webhook"
    assert sent_payload[0]["username"] == "sticky-bot"


@pytest.mark.asyncio
async def test_sticky_message_does_not_loop_on_own_bot_message(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello", message_id=10)

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky here",
                        "delay": 0,
                        "trigger_bot_message": True,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    await cog.on_message(message)
    assert len(guild_channel.sent) == 1

    self_post = FakeMessage(guild, guild_channel, FakeUser(999, bot=True), "sticky here", message_id=9001)
    await cog.on_message(self_post)
    assert len(guild_channel.sent) == 1


@pytest.mark.asyncio
async def test_sticky_message_deletes_previous_after_restart_using_db_cache(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello")

    bot1 = FakeBot()
    cog1 = StickyAutoCog(bot1)
    cog1.tick_meter = FakeTickMeter()
    await cog1.cog_load()
    await cog1.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog1.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky here",
                        "delay": 0,
                        "trigger_bot_message": False,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )
    await cog1.on_message(message)
    runtime = await cog1.storage.get_sticky_runtime(1234, 777)
    assert runtime is not None
    first_message_id = runtime.message_id
    assert first_message_id in guild_channel._stored

    bot2 = FakeBot()
    cog2 = StickyAutoCog(bot2)
    cog2.tick_meter = FakeTickMeter()
    await cog2.cog_load()
    await cog2.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog2.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky here",
                        "delay": 0,
                        "trigger_bot_message": False,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )
    await cog2.on_message(message)
    assert guild_channel._stored[first_message_id].deleted is True


@pytest.mark.asyncio
async def test_sticky_message_does_not_loop_on_own_webhook_message(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello", message_id=20)

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.insert_utility_webhook(
        ref_id="wh-1",
        guild_id=1234,
        channel_id=777,
        webhook_id=5000,
        webhook_token="token-x",
        tag="sticky",
    )
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky via webhook",
                        "delay": 0,
                        "trigger_bot_message": True,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "webhook", "webhook": {"name": "sticky-bot", "icon": None, "webhook": "wh-1"}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    class FakeWebhook:
        async def send(self, **kwargs):
            return FakeSentMessage(11111)

    monkeypatch.setattr("cogs.sticky_auto.discord.Webhook.partial", lambda *args, **kwargs: FakeWebhook())
    await cog.on_message(message)
    assert cog._sticky_message_ids[(1234, 777)] == 11111

    self_webhook_post = FakeMessage(guild, guild_channel, FakeUser(222, bot=False), "sticky via webhook", message_id=11111, webhook_id=5000)
    await cog.on_message(self_webhook_post)
    assert cog._sticky_message_ids[(1234, 777)] == 11111

    webhook_followup = FakeMessage(guild, guild_channel, FakeUser(222, bot=False), "another", message_id=22222, webhook_id=5000)
    await cog.on_message(webhook_followup)
    assert cog._sticky_message_ids[(1234, 777)] == 11111


@pytest.mark.asyncio
async def test_sticky_message_signature_match_blocks_unknown_webhook_loop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello", message_id=30)

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky via webhook",
                        "delay": 0,
                        "trigger_bot_message": True,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    await cog.on_message(message)
    assert len(guild_channel.sent) == 1

    unknown_webhook_post = FakeMessage(
        guild,
        guild_channel,
        FakeUser(333, bot=False),
        "sticky via webhook",
        message_id=33333,
        webhook_id=999999,
        embeds=[],
    )
    await cog.on_message(unknown_webhook_post)
    assert len(guild_channel.sent) == 1


@pytest.mark.asyncio
async def test_sticky_message_signature_cache_blocks_loop_after_config_change(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello", message_id=40)

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky-old",
                        "delay": 0,
                        "trigger_bot_message": True,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    await cog.on_message(message)
    assert len(guild_channel.sent) == 1
    sent_content = guild_channel.sent[-1][0]
    assert sent_content == "sticky-old"

    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky-new",
                        "delay": 0,
                        "trigger_bot_message": True,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    self_post = FakeMessage(guild, guild_channel, FakeUser(999, bot=True), "sticky-old", message_id=5001, embeds=[])
    await cog.on_message(self_post)
    assert len(guild_channel.sent) == 1


@pytest.mark.asyncio
async def test_sticky_message_notfound_clears_runtime_and_continues(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello")
    guild_channel._stored[9999] = FakePrevMessageNotFound(9999)

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.upsert_sticky_runtime(1234, 777, 9999, None)
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky here",
                        "delay": 0,
                        "trigger_bot_message": False,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    await cog.on_message(message)
    runtime = await cog.storage.get_sticky_runtime(1234, 777)
    assert runtime is not None
    assert runtime.message_id != 9999
    assert len(guild_channel.sent) == 1


@pytest.mark.asyncio
async def test_sticky_message_delete_http_error_stops_send_and_logs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello")
    guild_channel._stored[8888] = FakePrevMessageHttpError(8888)

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.upsert_sticky_runtime(1234, 777, 8888, None)
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky here",
                        "delay": 0,
                        "trigger_bot_message": False,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    await cog.on_message(message)
    assert len(guild_channel.sent) == 0
    runtime = await cog.storage.get_sticky_runtime(1234, 777)
    assert runtime is not None
    assert runtime.message_id == 8888
    logs = await cog.storage.fetch_logs("system", 1234, 20)
    assert any(log.section == "sticky-message" and log.result == "sticky-delete-failed" for log in logs)


@pytest.mark.asyncio
async def test_sticky_message_fetch_unsupported_stops_send_and_logs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannelNoFetch(777)
    guild = FakeGuild(1234, guild_channel)
    user = FakeUser(1, bot=False)
    message = FakeMessage(guild, guild_channel, user, "hello")

    await cog.storage.upsert_config("guild", 1234, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": True, "auto_reaction": False}))
    await cog.storage.upsert_sticky_runtime(1234, 777, 7777, None)
    await cog.storage.upsert_config(
        "guild",
        1234,
        "sticky-message",
        _envelope(
            {
                "items": [
                    {
                        "id": 1,
                        "message": "sticky here",
                        "delay": 0,
                        "trigger_bot_message": False,
                        "channels": [{"id": 1, "channel_id": 777, "send_mode": "bot", "webhook": {"name": "", "icon": None, "webhook": None}}],
                        "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
                    }
                ]
            }
        ),
    )

    await cog.on_message(message)
    assert len(guild_channel.sent) == 0
    logs = await cog.storage.fetch_logs("system", 1234, 20)
    assert any(log.section == "sticky-message" and log.result == "sticky-delete-failed" for log in logs)


@pytest.mark.asyncio
async def test_auto_reaction_adds_all_emojis_with_tick(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    cog.tick_meter = FakeTickMeter()
    await cog.cog_load()

    guild_channel = FakeChannel(888)
    guild = FakeGuild(2233, guild_channel)
    custom = object()
    guild.add_emoji(123456, custom)
    user = FakeUser(10, bot=False)
    message = FakeMessage(guild, guild_channel, user, "world")

    await cog.storage.upsert_config("guild", 2233, "management-module", _envelope({"welcome": True, "level": True, "sticky_message": False, "auto_reaction": True}))
    await cog.storage.upsert_config(
        "guild",
        2233,
        "auto-reaction",
        _envelope({"rules": [{"id": 1, "channels": [888], "emojis": ["🔥", "<:x:123456>"]}]}),
    )

    await cog.on_message(message)
    assert len(message.added_reactions) == 2
    assert "🔥" in message.added_reactions
    assert custom in message.added_reactions
    add_ticks = [call for call in cog.tick_meter.consume_calls if call[1] == "auto_reaction.add"]
    assert len(add_ticks) == 2


@pytest.mark.asyncio
async def test_sticky_embed_conversion_failures_are_logged(monkeypatch, tmp_path, caplog):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = StickyAutoCog(bot)
    await cog.cog_load()

    class BadEmbed:
        def to_dict(self):
            raise RuntimeError("broken to_dict")

    def raise_from_dict(_payload):
        raise RuntimeError("broken from_dict")

    monkeypatch.setattr("cogs.sticky_auto.discord.Embed.from_dict", raise_from_dict)
    caplog.set_level(logging.WARNING)
    assert cog._first_embed_signature([BadEmbed()]) is None
    assert cog._embed_from_dict({"title": "ok"}) is None

    messages = [record.getMessage() for record in caplog.records]
    assert any("sticky embed signature conversion failed" in message for message in messages)
    assert any("sticky embed reconstruction failed" in message for message in messages)
