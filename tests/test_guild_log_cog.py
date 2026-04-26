from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cogs.guild_log import GuildLogCog
from utils.storage import Storage


async def _noop_wait():
    return None


class FakeReady:
    async def wait(self):
        await _noop_wait()


class FakeBot:
    def __init__(self) -> None:
        self.config_bind_ready = FakeReady()
        self.ensure_calls = 0
        self._guilds: dict[int, FakeGuild] = {}

    async def ensure_config_bound(self):
        self.ensure_calls += 1

    def get_guild(self, guild_id: int):
        return self._guilds.get(guild_id)


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent: list[tuple[str | None, object | None]] = []

    async def send(self, content: str | None = None, embed=None):
        self.sent.append((content, embed))


class FakeGuild:
    def __init__(self, guild_id: int, channels: dict[int, FakeChannel]) -> None:
        self.id = guild_id
        self._channels = channels

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        return self._channels[channel_id]


@dataclass
class FakeRole:
    id: int


class FakeAvatar:
    def __init__(self, url: str) -> None:
        self.url = url


class FakeMember:
    def __init__(
        self,
        *,
        guild: FakeGuild,
        user_id: int,
        name: str,
        mention: str,
        bot: bool = False,
        nick: str | None = None,
        roles: list[int] | None = None,
        avatar_url: str = "https://example.com/a.png",
        communication_disabled_until=None,
    ) -> None:
        self.guild = guild
        self.id = user_id
        self.name = name
        self.mention = mention
        self.bot = bot
        self.nick = nick
        self.roles = [FakeRole(role_id) for role_id in (roles or [])]
        self.display_avatar = FakeAvatar(avatar_url)
        self.communication_disabled_until = communication_disabled_until


class FakeUser:
    def __init__(self, user_id: int, mention: str, bot: bool = False) -> None:
        self.id = user_id
        self.mention = mention
        self.bot = bot


class FakeVoiceState:
    def __init__(self, mute: bool) -> None:
        self.mute = mute


class FakeMessage:
    def __init__(
        self,
        *,
        guild: FakeGuild,
        author: FakeMember,
        channel_id: int,
        message_id: int,
        content: str,
    ) -> None:
        self.guild = guild
        self.author = author
        self.channel = SimpleNamespace(id=channel_id)
        self.id = message_id
        self.content = content


class FakeRawDeletePayload:
    def __init__(self, *, guild_id: int, channel_id: int, message_id: int, cached_message=None) -> None:
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.cached_message = cached_message


class FakeRawEditPayload:
    def __init__(self, *, guild_id: int, message_id: int, data: dict, cached_message=None) -> None:
        self.guild_id = guild_id
        self.message_id = message_id
        self.data = data
        self.cached_message = cached_message


@pytest.mark.asyncio
async def test_message_delete_log_output(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(10)
    guild = FakeGuild(1234, {10: channel})
    bot._guilds[guild.id] = guild
    member = FakeMember(guild=guild, user_id=111, name="user-a", mention="<@111>")
    message = FakeMessage(guild=guild, author=member, channel_id=20, message_id=999, content="hello")

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "message_log": {
                        "channel": 10,
                        "categories": ["delete"],
                        "tracking_message_count": 1000,
                    },
                    "member_log": {"channel": None, "categories": []},
                    "mod_log": {"channel": None, "types": []},
                },
                "startup_payload": {
                    "message_log": {"channel": 10, "categories": ["delete"], "tracking_message_count": 1000},
                    "member_log": {"channel": None, "categories": []},
                    "mod_log": {"channel": None, "types": []},
                },
            },
        },
    )

    await cog.on_message_delete(message)
    assert len(channel.sent) == 1
    _, embed = channel.sent[0]
    assert embed.title == "Message Deleted"
    field_map = {field.name: field.value for field in embed.fields}
    assert field_map["Message ID"] == "999"


@pytest.mark.asyncio
async def test_message_edit_log_output(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(10)
    guild = FakeGuild(1234, {10: channel})
    bot._guilds[guild.id] = guild
    member = FakeMember(guild=guild, user_id=111, name="user-a", mention="<@111>")
    before = FakeMessage(guild=guild, author=member, channel_id=20, message_id=999, content="before")
    after = FakeMessage(guild=guild, author=member, channel_id=20, message_id=999, content="after")

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "message_log": {
                        "channel": 10,
                        "categories": ["edit"],
                        "tracking_message_count": 1000,
                    },
                    "member_log": {"channel": None, "categories": []},
                    "mod_log": {"channel": None, "types": []},
                },
                "startup_payload": {
                    "message_log": {"channel": 10, "categories": ["edit"], "tracking_message_count": 1000},
                    "member_log": {"channel": None, "categories": []},
                    "mod_log": {"channel": None, "types": []},
                },
            },
        },
    )

    await cog.on_message_edit(before, after)
    assert len(channel.sent) == 1
    _, embed = channel.sent[0]
    assert embed.title == "Message Edited"
    field_map = {field.name: field.value for field in embed.fields}
    assert field_map["Before"] == "before"
    assert field_map["After"] == "after"


@pytest.mark.asyncio
async def test_member_join_leave_logs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(11)
    guild = FakeGuild(1234, {11: channel})
    bot._guilds[guild.id] = guild
    member = FakeMember(guild=guild, user_id=222, name="user-b", mention="<@222>")

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": 11, "categories": ["join", "leave"]},
                    "mod_log": {"channel": None, "types": []},
                },
                "startup_payload": {
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": 11, "categories": ["join", "leave"]},
                    "mod_log": {"channel": None, "types": []},
                },
            },
        },
    )

    await cog.on_member_join(member)
    await cog.on_member_remove(member)
    assert len(channel.sent) == 2
    assert channel.sent[0][1].title == "Member Joined"
    assert channel.sent[1][1].title == "Member Left"


@pytest.mark.asyncio
async def test_member_update_logs_nickname_role_avatar(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(11)
    guild = FakeGuild(1234, {11: channel})
    bot._guilds[guild.id] = guild
    before = FakeMember(
        guild=guild,
        user_id=333,
        name="user-c",
        mention="<@333>",
        nick="old",
        roles=[1, 2],
        avatar_url="https://example.com/old.png",
    )
    after = FakeMember(
        guild=guild,
        user_id=333,
        name="user-c",
        mention="<@333>",
        nick="new",
        roles=[2, 3],
        avatar_url="https://example.com/new.png",
    )

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": 11, "categories": ["nickname", "role", "avatar"]},
                    "mod_log": {"channel": None, "types": []},
                },
                "startup_payload": {
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": 11, "categories": ["nickname", "role", "avatar"]},
                    "mod_log": {"channel": None, "types": []},
                },
            },
        },
    )

    await cog.on_member_update(before, after)
    assert len(channel.sent) == 3
    assert channel.sent[0][1].title == "Member Nickname Updated"
    assert channel.sent[1][1].title == "Member Roles Updated"
    assert channel.sent[2][1].title == "Member Avatar Updated"


@pytest.mark.asyncio
async def test_mod_log_ban_unban_outputs_embed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(12)
    guild = FakeGuild(1234, {12: channel})
    bot._guilds[guild.id] = guild
    user = FakeUser(777, "<@777>")

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "mod_log": {"channel": 12, "types": ["ban", "unban"]},
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": None, "categories": []},
                },
                "startup_payload": {
                    "mod_log": {"channel": 12, "types": ["ban", "unban"]},
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": None, "categories": []},
                },
            },
        },
    )

    await cog.on_member_ban(guild, user)
    await cog.on_member_unban(guild, user)
    assert len(channel.sent) == 2
    assert channel.sent[0][1].title == "Member Banned"
    assert channel.sent[1][1].title == "Member Unbanned"


@pytest.mark.asyncio
async def test_mod_log_timeout_outputs_embed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(12)
    guild = FakeGuild(1234, {12: channel})
    bot._guilds[guild.id] = guild
    now = datetime.now(timezone.utc)
    before = FakeMember(
        guild=guild,
        user_id=888,
        name="u1",
        mention="<@888>",
        communication_disabled_until=None,
    )
    after = FakeMember(
        guild=guild,
        user_id=888,
        name="u1",
        mention="<@888>",
        communication_disabled_until=now + timedelta(minutes=10),
    )

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "mod_log": {"channel": 12, "types": ["timeout"]},
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": None, "categories": []},
                },
                "startup_payload": {
                    "mod_log": {"channel": 12, "types": ["timeout"]},
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": None, "categories": []},
                },
            },
        },
    )

    await cog.on_member_update(before, after)
    assert len(channel.sent) == 1
    assert channel.sent[0][1].title == "Member Timed Out"


@pytest.mark.asyncio
async def test_mod_log_mute_unmute_outputs_embed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(12)
    guild = FakeGuild(1234, {12: channel})
    bot._guilds[guild.id] = guild
    member = FakeMember(guild=guild, user_id=999, name="u2", mention="<@999>")

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "mod_log": {"channel": 12, "types": ["mute", "unmute"]},
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": None, "categories": []},
                },
                "startup_payload": {
                    "mod_log": {"channel": 12, "types": ["mute", "unmute"]},
                    "message_log": {"channel": None, "categories": [], "tracking_message_count": 1000},
                    "member_log": {"channel": None, "categories": []},
                },
            },
        },
    )

    await cog.on_voice_state_update(member, FakeVoiceState(mute=False), FakeVoiceState(mute=True))
    await cog.on_voice_state_update(member, FakeVoiceState(mute=True), FakeVoiceState(mute=False))
    assert len(channel.sent) == 2
    assert channel.sent[0][1].title == "Member Server Muted"
    assert channel.sent[1][1].title == "Member Server Unmuted"


@pytest.mark.asyncio
async def test_raw_delete_uses_inmemory_cache_when_discord_cache_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(13)
    guild = FakeGuild(1234, {13: channel})
    bot._guilds[guild.id] = guild
    member = FakeMember(guild=guild, user_id=111, name="user-a", mention="<@111>")
    message = FakeMessage(guild=guild, author=member, channel_id=20, message_id=1001, content="cached content")

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "mod_log": {"channel": None, "types": []},
                    "message_log": {
                        "channel": 13,
                        "categories": ["delete"],
                        "tracking_message_count": 1000,
                        "tracking_message_mode": "extra",
                    },
                    "member_log": {"channel": None, "categories": []},
                },
                "startup_payload": {
                    "mod_log": {"channel": None, "types": []},
                    "message_log": {
                        "channel": 13,
                        "categories": ["delete"],
                        "tracking_message_count": 1000,
                        "tracking_message_mode": "extra",
                    },
                    "member_log": {"channel": None, "categories": []},
                },
            },
        },
    )

    await cog.on_message(message)
    payload = FakeRawDeletePayload(guild_id=1234, channel_id=20, message_id=1001, cached_message=None)
    await cog.on_raw_message_delete(payload)
    assert len(channel.sent) == 1
    _, embed = channel.sent[0]
    assert embed.title == "Message Deleted"
    field_map = {field.name: field.value for field in embed.fields}
    assert field_map["Content"] == "cached content"


@pytest.mark.asyncio
async def test_raw_edit_uses_inmemory_cache_when_discord_cache_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(14)
    guild = FakeGuild(1234, {14: channel})
    bot._guilds[guild.id] = guild
    member = FakeMember(guild=guild, user_id=222, name="user-b", mention="<@222>")
    message = FakeMessage(guild=guild, author=member, channel_id=21, message_id=2002, content="before-text")

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "mod_log": {"channel": None, "types": []},
                    "message_log": {
                        "channel": 14,
                        "categories": ["edit"],
                        "tracking_message_count": 1000,
                        "tracking_message_mode": "extra",
                    },
                    "member_log": {"channel": None, "categories": []},
                },
                "startup_payload": {
                    "mod_log": {"channel": None, "types": []},
                    "message_log": {
                        "channel": 14,
                        "categories": ["edit"],
                        "tracking_message_count": 1000,
                        "tracking_message_mode": "extra",
                    },
                    "member_log": {"channel": None, "categories": []},
                },
            },
        },
    )

    await cog.on_message(message)
    payload = FakeRawEditPayload(guild_id=1234, message_id=2002, data={"content": "after-text"}, cached_message=None)
    await cog.on_raw_message_edit(payload)
    assert len(channel.sent) == 1
    _, embed = channel.sent[0]
    assert embed.title == "Message Edited"
    field_map = {field.name: field.value for field in embed.fields}
    assert field_map["Before"] == "before-text"
    assert field_map["After"] == "after-text"


@pytest.mark.asyncio
async def test_raw_message_paths_do_not_use_inmemory_cache_in_normal_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    cog = GuildLogCog(bot)
    await cog.cog_load()

    channel = FakeChannel(15)
    guild = FakeGuild(1234, {15: channel})
    bot._guilds[guild.id] = guild
    member = FakeMember(guild=guild, user_id=333, name="user-c", mention="<@333>")
    message = FakeMessage(guild=guild, author=member, channel_id=21, message_id=3003, content="before")

    await cog.storage.upsert_config(
        "guild",
        1234,
        "guild-log",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "mod_log": {"channel": None, "types": []},
                    "message_log": {
                        "channel": 15,
                        "categories": ["edit", "delete"],
                        "tracking_message_count": 1000,
                        "tracking_message_mode": "normal",
                    },
                    "member_log": {"channel": None, "categories": []},
                },
                "startup_payload": {
                    "mod_log": {"channel": None, "types": []},
                    "message_log": {
                        "channel": 15,
                        "categories": ["edit", "delete"],
                        "tracking_message_count": 1000,
                        "tracking_message_mode": "normal",
                    },
                    "member_log": {"channel": None, "categories": []},
                },
            },
        },
    )

    await cog.on_message(message)
    await cog.on_raw_message_edit(FakeRawEditPayload(guild_id=1234, message_id=3003, data={"content": "after"}))
    await cog.on_raw_message_delete(FakeRawDeletePayload(guild_id=1234, channel_id=21, message_id=3003))
    assert len(channel.sent) == 0
