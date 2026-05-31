from __future__ import annotations

import pytest

from cogs.level import LevelCog
from utils.config_bind import bind_all_settings
from utils.storage import LevelUserRow, Storage


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


class FakeBot:
    def __init__(self, guild_ids: list[int]) -> None:
        self.guilds = [FakeGuild(guild_id) for guild_id in guild_ids]
        self.user = None
        self.config_bind_ready = type("Ready", (), {"wait": staticmethod(_noop_wait)})()
        self.ensure_bind_calls = 0

    def get_guild(self, _guild_id: int):
        return None

    async def ensure_config_bound(self):
        self.ensure_bind_calls += 1


async def _noop_wait():
    return None


class FakeMember:
    def __init__(self, *, name: str, mention: str, nick: str | None = None, user_text: str | None = None) -> None:
        self.name = name
        self.mention = mention
        self.nick = nick
        self._user_text = user_text or name

    @property
    def display_name(self) -> str:
        return self.nick if self.nick else self.name

    def __str__(self) -> str:
        return self._user_text


class FakeSendChannel:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, content=None, **kwargs):
        self.sent.append({"content": content, **kwargs})


@pytest.mark.asyncio
async def test_bind_all_settings_preloads_sections(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await bind_all_settings(storage, [1234])

    # representative guild sections
    assert await storage.load_config("guild", 1234, "level-common") is not None
    assert await storage.load_config("guild", 1234, "management-module") is not None
    assert await storage.load_config("guild", 1234, "guild-log") is not None

    # representative root sections
    assert await storage.load_config("root", 0, "root-defaults") is not None
    assert await storage.load_config("root", 0, "tenant-connection") is not None
    system_rows = await storage.fetch_logs("system", scope_id=0, limit=20)
    messages = [row.result for row in system_rows]
    assert "bind-completed" in messages
    assert "bind-started" in messages

    guild_rows = await storage.fetch_logs("system", scope_id=1234, limit=20)
    guild_messages = [row.result for row in guild_rows]
    assert "bind-completed" in guild_messages
    assert "bind-started" in guild_messages


@pytest.mark.asyncio
async def test_level_cog_waits_for_global_bind(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()
    await cog._ensure_bind_ready()
    assert bot.ensure_bind_calls >= 1


@pytest.mark.asyncio
async def test_level_rank_progress_bar_and_percent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()

    assert cog._progress_percent(total_xp=150, floor_xp=100, next_xp=200) == 50.0
    assert cog._progress_bar(50.0) == ("█" * 10 + "░" * 10)
    assert cog._progress_bar(100.0) == ("█" * 20)


@pytest.mark.asyncio
async def test_build_rank_embed_uses_table_thresholds(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()

    await cog.storage.replace_level_table(
        1234,
        [
            {"level": 1, "required_total_xp": 100, "delta_xp": 100, "segment": "fixed"},
            {"level": 2, "required_total_xp": 250, "delta_xp": 150, "segment": "fixed"},
            {"level": 3, "required_total_xp": 500, "delta_xp": 250, "segment": "fixed"},
        ],
    )
    embed = await cog._build_rank_embed(
        1234,
        {"level": 1, "total_xp": 175, "next_level_xp": 250, "rank": 7},
    )
    assert embed.title == "Level Status"
    progress_field = next(field for field in embed.fields if field.name == "Progress")
    assert "50.0%" in progress_field.value
    assert "remain=75" in progress_field.value
    assert next(field for field in embed.fields if field.name == "Total XP").value == "175"
    assert next(field for field in embed.fields if field.name == "Rank").value == "#7"
    assert next(field for field in embed.fields if field.name == "Next Level").value == "250 XP"
    assert next(field for field in embed.fields if field.name == "Remaining").value == "75 XP"


@pytest.mark.asyncio
async def test_build_ranking_embed_uses_discord_readable_table(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()

    rows = [
        LevelUserRow(guild_id=1234, user_id=100, total_xp=12345, level=12, updated_at="now"),
        LevelUserRow(guild_id=1234, user_id=200, total_xp=900, level=3, updated_at="now"),
    ]
    embed = cog._build_ranking_embed(rows, requested_limit=10)

    assert embed.title == "Level Ranking"
    assert "rank  user" in embed.description
    assert "100" in embed.description
    assert "12345" in embed.description
    assert embed.footer.text == "Showing 2 ranked users"


@pytest.mark.asyncio
async def test_levelup_message_username_placeholder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()
    member = FakeMember(name="Saroniii", mention="<@1>", user_text="Saroniii#1234")

    message = cog._render_levelup_message(
        level_common={"levelup_message": "GG {username}! level={level}"},
        member=member,
        old_level=1,
        new_level=2,
        total_xp=123,
    )
    assert message == "GG Saroniii! level=2"


@pytest.mark.asyncio
async def test_levelup_send_disables_everyone_and_role_mentions(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()
    channel = FakeSendChannel()

    async def _resolve(_guild, _channel_id):
        return channel

    monkeypatch.setattr("cogs.level.resolve_guild_channel", _resolve)
    monkeypatch.setattr("cogs.level.discord.TextChannel", FakeSendChannel)
    await cog.storage.upsert_config(
        "guild",
        1234,
        "level-common",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {"levelup_channel": 99, "levelup_message": "@everyone <@&1> {mention}"},
                "startup_payload": {"levelup_channel": 99, "levelup_message": "@everyone <@&1> {mention}"},
            },
        },
    )
    member = FakeMember(name="Saroniii", mention="<@1>")
    await cog._maybe_notify_levelup(FakeGuild(1234), member, 1, 2, 100)

    assert channel.sent
    allowed = channel.sent[0]["allowed_mentions"]
    assert allowed.everyone is False
    assert allowed.roles is False
    assert allowed.users is True


@pytest.mark.asyncio
async def test_levelup_message_user_placeholder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()
    member = FakeMember(name="Saroniii", mention="<@1>", user_text="Saroniii#1234")

    message = cog._render_levelup_message(
        level_common={"levelup_message": "{user} reached {level}"},
        member=member,
        old_level=1,
        new_level=2,
        total_xp=123,
    )
    assert message == "Saroniii#1234 reached 2"


@pytest.mark.asyncio
async def test_levelup_message_nickname_fallback_to_username(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()
    member = FakeMember(name="Saroniii", mention="<@1>", nick=None)

    message = cog._render_levelup_message(
        level_common={"levelup_message": "{nickname} -> {level}"},
        member=member,
        old_level=1,
        new_level=2,
        total_xp=123,
    )
    assert message == "Saroniii -> 2"


@pytest.mark.asyncio
async def test_levelup_message_nickname_uses_nick_when_present(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot([1234])
    cog = LevelCog(bot)
    await cog.cog_load()
    member = FakeMember(name="Saroniii", mention="<@1>", nick="サロにぃ")

    message = cog._render_levelup_message(
        level_common={"levelup_message": "{nickname} -> {level}"},
        member=member,
        old_level=1,
        new_level=2,
        total_xp=123,
    )
    assert message == "サロにぃ -> 2"
