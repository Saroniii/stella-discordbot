from __future__ import annotations

import asyncio

import pytest

from cogs.cli import CliCog
from utils.cli.types import EngineContext, ScopeType, SessionContext


class FakeMemberBase:
    pass


class FakePermissions:
    def __init__(self, manage_guild: bool) -> None:
        self.manage_guild = manage_guild


class FakeMember(FakeMemberBase):
    def __init__(self, user_id: int, display_name: str, manage_guild: bool) -> None:
        self.id = user_id
        self.display_name = display_name
        self.guild_permissions = FakePermissions(manage_guild=manage_guild)


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self._emoji_ids: set[int] = set()
        self._channels: dict[int, object] = {}

    def add_emoji(self, emoji_id: int) -> None:
        self._emoji_ids.add(emoji_id)

    def get_emoji(self, emoji_id: int):
        return object() if emoji_id in self._emoji_ids else None

    def add_channel(self, channel: object) -> None:
        channel_id = getattr(channel, "id", None)
        if isinstance(channel_id, int):
            self._channels[channel_id] = channel

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        return self._channels.get(channel_id)


class FakeGuildFetchFails(FakeGuild):
    async def fetch_channel(self, channel_id: int):
        _ = channel_id
        raise RuntimeError("fetch failed")


class FakeThread:
    def __init__(self, thread_id: int, name: str | None = None) -> None:
        self.id = thread_id
        self.name = name or f"stella-cli-{thread_id}"
        self.sent_messages: list[str] = []
        self.sent_files: list[object] = []
        self.deleted = False
        self.last_message_id: int | None = None

    async def send(self, content: str | None = None, *, file: object | None = None) -> None:
        if content is not None:
            self.sent_messages.append(content)
        if file is not None:
            self.sent_files.append(file)
        self.last_message_id = (self.last_message_id or 0) + 1

    async def delete(self) -> None:
        self.deleted = True


class FakeTextChannel:
    def __init__(self, channel_id: int, threads: list[FakeThread] | None = None) -> None:
        self.id = channel_id
        self.threads = list(threads or [])
        self.nsfw = False

    async def archived_threads(self, limit=None, private=False):
        _ = (limit, private)
        for thread in []:
            yield thread

    def is_nsfw(self) -> bool:
        return bool(self.nsfw)


class FakeBrokenArchivedTextChannel(FakeTextChannel):
    async def archived_threads(self, limit=None, private=False):
        _ = (limit, private)
        if False:
            yield None
        raise RuntimeError("archived scan failed")


class FakeDeletableMessage:
    async def delete(self) -> None:
        raise RuntimeError("delete failed")


class FakeFetchMessageChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id

    async def fetch_message(self, _message_id: int):
        return FakeDeletableMessage()


class FakeNoFetchChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class FakeBrokenNsfwChannel(FakeTextChannel):
    def is_nsfw(self) -> bool:
        raise RuntimeError("nsfw check failed")


class FakeCommandMessage:
    def __init__(self, thread: FakeThread | None = None) -> None:
        self._thread = thread

    async def create_thread(self, name: str) -> FakeThread:
        if self._thread is None:
            raise RuntimeError("thread unavailable")
        return self._thread


class FakeIncomingMessage:
    def __init__(self, author: FakeMember, channel: FakeThread, content: str) -> None:
        self.author = author
        self.channel = channel
        self.content = content
        self.reactions: list[str] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)


class FakeIncomingMessageReactionFail(FakeIncomingMessage):
    async def add_reaction(self, emoji: str) -> None:
        _ = emoji
        raise RuntimeError("reaction failed")


class FakeContext:
    def __init__(self, guild: FakeGuild | None, author: FakeMember | object, message: object, channel: object | None = None) -> None:
        self.guild = guild
        self.author = author
        self.message = message
        self.channel = channel
        self.sent_messages: list[str] = []

    async def send(self, content: str) -> None:
        self.sent_messages.append(content)


class FakeBot:
    def __init__(self, incoming: list[FakeIncomingMessage | BaseException] | None = None) -> None:
        self._incoming = list(incoming or [])
        self._guilds: dict[int, FakeGuild] = {}

    async def wait_for(self, event_name: str, timeout: int, check):
        assert event_name == "message"
        if not self._incoming:
            raise AssertionError("wait_for called with no prepared messages")
        value = self._incoming.pop(0)
        if isinstance(value, BaseException):
            raise value
        assert check(value) is True
        return value

    def get_guild(self, _guild_id: int):
        return self._guilds.get(_guild_id)

    @property
    def guilds(self):
        return list(self._guilds.values())

    async def fetch_guild(self, guild_id: int):
        guild = self._guilds.get(guild_id)
        if guild is None:
            raise AssertionError("guild not found")
        return guild

    def get_channel(self, _channel_id: int):
        return None

    async def fetch_channel(self, _channel_id: int):
        raise AssertionError("fetch_channel should not be used in these tests")


async def invoke_cli(cog: CliCog, ctx: FakeContext) -> None:
    await cog.cli.callback(cog, ctx)


@pytest.mark.asyncio
async def test_cli_happy_path_runs_wait_for_loop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(1001)
    author = FakeMember(user_id=5001, display_name="tester", manage_guild=True)
    thread = FakeThread(thread_id=9001)
    incoming = [
        FakeIncomingMessage(author=author, channel=thread, content="enter welcome"),
        FakeIncomingMessage(author=author, channel=thread, content="set join-roles 1 2"),
        FakeIncomingMessage(author=author, channel=thread, content="deploy"),
        FakeIncomingMessage(author=author, channel=thread, content="quit"),
    ]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    assert ctx.sent_messages == []
    assert len(thread.sent_messages) >= 5
    assert "CLI session started." in thread.sent_messages[0]
    assert "stella(guild:1001)>" in thread.sent_messages[0]
    assert "session closed" in thread.sent_messages[-1]
    assert cog.sessions.get(guild.id) is None

    row = await cog.storage.load_config("guild", guild.id, "welcome")
    assert row is not None
    startup = row.data["payload"]["startup_payload"]
    assert startup["join_roles"] == [1, 2]


@pytest.mark.asyncio
async def test_cli_accepts_multiline_single_message(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(1002)
    author = FakeMember(user_id=5002, display_name="tester2", manage_guild=True)
    thread = FakeThread(thread_id=9002)
    incoming = [
        FakeIncomingMessage(
            author=author,
            channel=thread,
            content="enter welcome\nset join-roles 11 22\ndeploy\nquit",
        )
    ]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    assert any("session closed" in message for message in thread.sent_messages)
    assert cog.sessions.get(guild.id) is None

    row = await cog.storage.load_config("guild", guild.id, "welcome")
    assert row is not None
    startup = row.data["payload"]["startup_payload"]
    assert startup["join_roles"] == [11, 22]


@pytest.mark.asyncio
async def test_cli_rejects_when_manage_guild_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(
        guild=FakeGuild(2001),
        author=FakeMember(user_id=77, display_name="nope", manage_guild=False),
        message=FakeCommandMessage(thread=FakeThread(thread_id=3001)),
        channel=FakeThread(thread_id=3001),
    )
    await invoke_cli(cog, ctx)
    assert ctx.sent_messages == ["Manage Guild permission is required."]


@pytest.mark.asyncio
async def test_cli_rejects_when_active_session_exists(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    guild = FakeGuild(3001)
    author = FakeMember(user_id=88, display_name="dup", manage_guild=True)
    existing = SessionContext(
        session_id="existing",
        guild_id=guild.id,
        thread_id=4444,
        actor_user_id=author.id,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    assert cog.sessions.acquire(guild.id, existing) is True

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=FakeThread(thread_id=9999)), channel=FakeThread(thread_id=9999))
    await invoke_cli(cog, ctx)
    assert ctx.sent_messages == ["An active CLI session already exists: <#4444>"]


@pytest.mark.asyncio
async def test_cli_timeout_closes_session(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(4001)
    author = FakeMember(user_id=99, display_name="timeout", manage_guild=True)
    thread = FakeThread(thread_id=5001)
    bot = FakeBot(incoming=[TimeoutError()])
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    assert any("Session timeout. CLI closed." in message for message in thread.sent_messages)
    assert cog.sessions.get(guild.id) is None


@pytest.mark.asyncio
async def test_cli_rejects_non_guild_context(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=None, author=object(), message=object(), channel=FakeThread(thread_id=1))
    await invoke_cli(cog, ctx)
    assert ctx.sent_messages == ["This command is guild-only."]


@pytest.mark.asyncio
async def test_format_output_blocks_splits_over_2000(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    blocks = cog._format_output_blocks("x" * 5000, "stella(guild:1)>")
    assert len(blocks) >= 3
    assert all(len(block) <= 2000 for block in blocks)
    assert "stella(guild:1)>" in blocks[-1]


@pytest.mark.asyncio
async def test_format_output_blocks_sanitizes_code_fences(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    blocks = cog._format_output_blocks("before ``` after", "stella(guild:1)>")
    assert len(blocks) == 1
    assert blocks[0].count("```") == 2
    assert "before `\u200b`\u200b` after" in blocks[0]


@pytest.mark.asyncio
async def test_format_output_blocks_splits_after_sanitizing_code_fences(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    blocks = cog._format_output_blocks("```" * 800, "stella(guild:1)>")
    assert len(blocks) >= 2
    assert all(len(block) <= 2000 for block in blocks)
    assert all(block.count("```") == 2 for block in blocks)
    assert "stella(guild:1)>" in blocks[-1]


@pytest.mark.asyncio
async def test_send_formatted_sends_multiple_messages(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    thread = FakeThread(thread_id=999)
    await cog._send_formatted(thread, "y" * 4500, "stella(guild:1)>")

    assert len(thread.sent_messages) >= 3
    assert all(message.startswith("```text\n") for message in thread.sent_messages)
    assert "stella(guild:1)>" in thread.sent_messages[-1]


@pytest.mark.asyncio
async def test_execute_config_requires_root_scope(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)
    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()
    session = SessionContext(
        session_id="cfg-scope",
        guild_id=10,
        thread_id=1,
        actor_user_id=1,
        scope_type=ScopeType.GUILD,
        scope_id=10,
    )
    ctx = EngineContext(actor_user_id=1, guild_id=10, channel_id=1, is_bot_admin=True, has_manage_guild=True)
    result = await cog._execute_utils(ctx, session, ["config", "deploy", "all-guilds"])
    assert "permission denied" in result


@pytest.mark.asyncio
async def test_execute_config_accepts_guild_target(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)
    bot = FakeBot()
    bot._guilds[11] = FakeGuild(11)
    cog = CliCog(bot)
    await cog.cog_load()
    session = SessionContext(
        session_id="cfg-root",
        guild_id=11,
        thread_id=1,
        actor_user_id=1,
        scope_type=ScopeType.ROOT,
        scope_id=0,
    )
    ctx = EngineContext(actor_user_id=1, guild_id=11, channel_id=1, is_bot_admin=True, has_manage_guild=True)

    class FakeResult:
        total = 1
        success = 1
        failed = 0
        details = ["guild=11 status=ok sections=3"]

    async def fake_rebind(_storage, guild_ids, mode):
        assert guild_ids == [11]
        assert mode == "full"
        return FakeResult()

    monkeypatch.setattr("cogs.cli.rebind_many_guilds", fake_rebind)
    result = await cog._execute_utils(ctx, session, ["config", "rebind", "full", "guild", "11"])
    assert "success=1" in result
    assert "guild=11 status=ok" in result


@pytest.mark.asyncio
async def test_execute_system_restart_calls_runtime_reload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)
    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()
    session = SessionContext(
        session_id="sys-root",
        guild_id=1,
        thread_id=1,
        actor_user_id=1,
        scope_type=ScopeType.ROOT,
        scope_id=0,
    )
    ctx = EngineContext(actor_user_id=1, guild_id=1, channel_id=1, is_bot_admin=True, has_manage_guild=True)

    calls: list[bool] = []

    async def fake_restart_runtime(*, keep_active_cli: bool = False):
        calls.append(keep_active_cli)

    setattr(bot, "restart_runtime", fake_restart_runtime)
    result = await cog._execute_utils(ctx, session, ["system", "restart"])
    assert result == "ok system restart completed"
    assert calls == [False]


@pytest.mark.asyncio
async def test_execute_system_restart_keep_active_calls_runtime_reload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)
    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()
    session = SessionContext(
        session_id="sys-root-keep",
        guild_id=1,
        thread_id=1,
        actor_user_id=1,
        scope_type=ScopeType.ROOT,
        scope_id=0,
    )
    ctx = EngineContext(actor_user_id=1, guild_id=1, channel_id=1, is_bot_admin=True, has_manage_guild=True)

    calls: list[bool] = []

    async def fake_restart_runtime(*, keep_active_cli: bool = False):
        calls.append(keep_active_cli)

    setattr(bot, "restart_runtime", fake_restart_runtime)
    result = await cog._execute_utils(ctx, session, ["system", "restart", "keep-active-cli"])
    assert result == "ok system restart completed (keep-active-cli)"
    assert calls == [True]


@pytest.mark.asyncio
async def test_validate_set_rejects_unavailable_custom_emoji(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(9991)
    bot = FakeBot()
    bot._guilds[guild.id] = guild
    cog = CliCog(bot)
    await cog.cog_load()

    session = SessionContext(
        session_id="s",
        guild_id=guild.id,
        thread_id=1,
        actor_user_id=1,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    ctx = EngineContext(actor_user_id=1, guild_id=guild.id, channel_id=1, is_bot_admin=False, has_manage_guild=True)

    err = await cog._validate_cli_set(ctx, session, "auto-reaction", "emojis", ["<:x:123456>"])
    assert err is not None
    assert "emoji unavailable" in err


@pytest.mark.asyncio
async def test_validate_set_accepts_available_custom_emoji(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(9992)
    guild.add_emoji(555666)
    bot = FakeBot()
    bot._guilds[guild.id] = guild
    cog = CliCog(bot)
    await cog.cog_load()

    session = SessionContext(
        session_id="s",
        guild_id=guild.id,
        thread_id=1,
        actor_user_id=1,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    ctx = EngineContext(actor_user_id=1, guild_id=guild.id, channel_id=1, is_bot_admin=False, has_manage_guild=True)

    err = await cog._validate_cli_set(ctx, session, "auto-reaction", "emojis", ["🔥", "<:x:555666>"])
    assert err is None


@pytest.mark.asyncio
async def test_cli_channel_mode_uses_invocation_channel_without_thread(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(7001)
    author = FakeMember(user_id=5010, display_name="channel-mode", manage_guild=True)
    channel = FakeThread(thread_id=7100, name="general")
    incoming = [FakeIncomingMessage(author=author, channel=channel, content="quit")]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    await cog.storage.upsert_config(
        "guild",
        guild.id,
        "console",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "always_print_help": False,
                    "console_mode": "channel",
                    "thread_console_after_delete": False,
                },
                "startup_payload": {
                    "always_print_help": False,
                    "console_mode": "channel",
                    "thread_console_after_delete": False,
                },
            },
        },
    )

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=None), channel=channel)
    await invoke_cli(cog, ctx)

    assert any("CLI session started." in message for message in channel.sent_messages)
    assert any("session closed" in message for message in channel.sent_messages)


@pytest.mark.asyncio
async def test_cli_thread_mode_auto_deletes_after_close(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    _orig_sleep = asyncio.sleep

    async def _fast_sleep(_seconds: float):
        await _orig_sleep(0)

    monkeypatch.setattr("cogs.cli.asyncio.sleep", _fast_sleep)

    guild = FakeGuild(7002)
    author = FakeMember(user_id=5011, display_name="thread-del", manage_guild=True)
    thread = FakeThread(thread_id=7200)
    incoming = [FakeIncomingMessage(author=author, channel=thread, content="quit")]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    await cog.storage.upsert_config(
        "guild",
        guild.id,
        "console",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "always_print_help": False,
                    "console_mode": "thread",
                    "thread_console_after_delete": True,
                },
                "startup_payload": {
                    "always_print_help": False,
                    "console_mode": "thread",
                    "thread_console_after_delete": True,
                },
            },
        },
    )

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert thread.deleted is True


@pytest.mark.asyncio
async def test_execute_console_thread_unused_remove(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    guild = FakeGuild(8001)
    keep_thread = FakeThread(thread_id=8101, name="stella-cli-active")
    stale_thread = FakeThread(thread_id=8102, name="stella-cli-stale")
    other_thread = FakeThread(thread_id=8103, name="other-topic")
    text_channel = FakeTextChannel(channel_id=8200, threads=[keep_thread, stale_thread, other_thread])
    guild.add_channel(text_channel)
    bot._guilds[guild.id] = guild

    active_session = SessionContext(
        session_id="active",
        guild_id=guild.id,
        thread_id=keep_thread.id,
        actor_user_id=1,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    assert cog.sessions.acquire(guild.id, active_session) is True

    ctx = EngineContext(actor_user_id=1, guild_id=guild.id, channel_id=text_channel.id, is_bot_admin=False, has_manage_guild=True)
    output = await cog._execute_console(ctx, active_session, ["thread", "unused", "remove", str(text_channel.id)])

    assert "status=deleted" in output
    assert stale_thread.deleted is True
    assert keep_thread.deleted is False
    assert other_thread.deleted is False


@pytest.mark.asyncio
async def test_execute_console_thread_unused_remove_logs_archived_scan_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    bot = FakeBot()
    cog = CliCog(bot)
    await cog.cog_load()

    guild = FakeGuild(8002)
    text_channel = FakeBrokenArchivedTextChannel(channel_id=8201, threads=[])
    guild.add_channel(text_channel)
    bot._guilds[guild.id] = guild

    active_session = SessionContext(
        session_id="active-broken-archived",
        guild_id=guild.id,
        thread_id=9999,
        actor_user_id=1,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    assert cog.sessions.acquire(guild.id, active_session) is True

    ctx = EngineContext(actor_user_id=1, guild_id=guild.id, channel_id=text_channel.id, is_bot_admin=False, has_manage_guild=True)
    output = await cog._execute_console(ctx, active_session, ["thread", "unused", "remove", str(text_channel.id)])
    assert "status=deleted" not in output
    logs = await cog.storage.fetch_logs("system", guild.id, 20)
    assert any(log.section == "console" and log.result == "thread-scan-failed" for log in logs)


@pytest.mark.asyncio
async def test_cli_log_to_file_start_no_message_response_and_stop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(9001)
    author = FakeMember(user_id=7777, display_name="logger", manage_guild=True)
    thread = FakeThread(thread_id=9002)
    incoming = [
        FakeIncomingMessage(author=author, channel=thread, content="execute cli to-file start no-message-response"),
        FakeIncomingMessage(author=author, channel=thread, content="where"),
        FakeIncomingMessage(author=author, channel=thread, content="execute cli to-file stop"),
        FakeIncomingMessage(author=author, channel=thread, content="quit"),
    ]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    assert any(name == "✅" for msg in incoming for name in msg.reactions)
    assert any(getattr(file, "filename", "").startswith("cli-log-") for file in thread.sent_files)
    exported = next(file for file in thread.sent_files if getattr(file, "filename", "").startswith("cli-log-"))
    payload = exported.fp.getvalue().decode("utf-8")
    assert "stella(guild:9001)> where" in payload
    assert "# stella cli session log" in payload


@pytest.mark.asyncio
async def test_cli_log_to_file_start_normal_mode_and_stop(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(9005)
    author = FakeMember(user_id=7779, display_name="logger3", manage_guild=True)
    thread = FakeThread(thread_id=9006)
    incoming = [
        FakeIncomingMessage(author=author, channel=thread, content="execute cli to-file start"),
        FakeIncomingMessage(author=author, channel=thread, content="where"),
        FakeIncomingMessage(author=author, channel=thread, content="execute cli to-file stop"),
        FakeIncomingMessage(author=author, channel=thread, content="quit"),
    ]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    assert all(msg.reactions == [] for msg in incoming)
    assert any("ok cli log started" in message for message in thread.sent_messages)
    assert any("scope=guild:9005 path=(top)" in message for message in thread.sent_messages)
    exported = next(file for file in thread.sent_files if getattr(file, "filename", "").startswith("cli-log-"))
    payload = exported.fp.getvalue().decode("utf-8")
    assert "stella(guild:9005)> where" in payload
    assert "stella(guild:9005)>\nstella(guild:9005)> execute cli to-file stop" in payload


@pytest.mark.asyncio
async def test_cli_log_to_file_auto_discard_on_quit(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(9003)
    author = FakeMember(user_id=7778, display_name="logger2", manage_guild=True)
    thread = FakeThread(thread_id=9004)
    incoming = [
        FakeIncomingMessage(author=author, channel=thread, content="execute cli to-file start no-message-response"),
        FakeIncomingMessage(author=author, channel=thread, content="quit"),
    ]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    assert thread.sent_files == []


@pytest.mark.asyncio
async def test_cli_log_no_message_response_multiline_keeps_stop_visible_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(9007)
    author = FakeMember(user_id=7780, display_name="logger4", manage_guild=True)
    thread = FakeThread(thread_id=9008)
    incoming = [
        FakeIncomingMessage(
            author=author,
            channel=thread,
            content=(
                "execute cli to-file start no-message-response\n"
                "where\n"
                "show\n"
                "execute cli to-file stop\n"
            ),
        ),
        FakeIncomingMessage(author=author, channel=thread, content="quit"),
    ]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    multiline_message = incoming[0]
    assert "✅" in multiline_message.reactions
    assert any("ok cli log stop requested" in msg for msg in thread.sent_messages)
    assert not any("scope=guild:9007 path=(top)" in msg for msg in thread.sent_messages)


@pytest.mark.asyncio
async def test_cli_log_no_message_response_unknown_command_still_marks_success(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(9009)
    author = FakeMember(user_id=7781, display_name="logger5", manage_guild=True)
    thread = FakeThread(thread_id=9010)
    incoming = [
        FakeIncomingMessage(author=author, channel=thread, content="execute cli to-file start no-message-response"),
        FakeIncomingMessage(author=author, channel=thread, content="unknown-command"),
        FakeIncomingMessage(author=author, channel=thread, content="execute cli to-file stop"),
        FakeIncomingMessage(author=author, channel=thread, content="quit"),
    ]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    assert "✅" in incoming[1].reactions
    assert "❌" not in incoming[1].reactions


@pytest.mark.asyncio
async def test_execute_chat_group_create_join_and_auth_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild1 = FakeGuild(6001)
    guild1.add_channel(FakeTextChannel(7001))
    guild2 = FakeGuild(6002)
    guild2.add_channel(FakeTextChannel(7002))
    bot._guilds[guild1.id] = guild1
    bot._guilds[guild2.id] = guild2

    cog = CliCog(bot)
    await cog.cog_load()

    leader_ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild1.id,
        channel_id=7001,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    leader_session = SessionContext(
        session_id="chat-group-leader",
        guild_id=guild1.id,
        thread_id=7001,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild1.id,
    )
    created = await cog._execute_utils(
        leader_ctx,
        leader_session,
        ["chat-group", "create", "name", "Test", "mode", "private", "channel", "7001"],
    )
    assert created.startswith("ok group-id=")
    group_id = created.split("group-id=", 1)[1].strip()

    auth = await cog._execute_utils(
        leader_ctx,
        leader_session,
        ["chat-group", "manage-group", group_id, "auth-key", "create", "guild", str(guild2.id)],
    )
    assert "auth-key-id=" in auth and "auth-key=" in auth
    auth_key = auth.split("auth-key=", 1)[1].strip()

    member_ctx = EngineContext(
        actor_user_id=200,
        guild_id=guild2.id,
        channel_id=7002,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    member_session = SessionContext(
        session_id="chat-group-member",
        guild_id=guild2.id,
        thread_id=7002,
        actor_user_id=200,
        scope_type=ScopeType.GUILD,
        scope_id=guild2.id,
    )
    joined = await cog._execute_utils(
        member_ctx,
        member_session,
        ["chat-group", "join", group_id, "channel", "7002", "auth-key", auth_key],
    )
    assert joined == f"ok joined group-id={group_id} mode=auth-key"


@pytest.mark.asyncio
async def test_execute_chat_group_rejects_nsfw_channel(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild = FakeGuild(6011)
    channel = FakeTextChannel(7011)
    channel.nsfw = True
    guild.add_channel(channel)
    bot._guilds[guild.id] = guild

    cog = CliCog(bot)
    await cog.cog_load()
    ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild.id,
        channel_id=7011,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    session = SessionContext(
        session_id="chat-group-nsfw",
        guild_id=guild.id,
        thread_id=7011,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    output = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "create", "name", "NG", "mode", "public", "channel", "7011"],
    )
    assert "nsfw not allowed" in output


@pytest.mark.asyncio
async def test_execute_chat_group_rejects_channel_when_nsfw_check_errors(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild = FakeGuild(6002)
    guild.add_channel(FakeBrokenNsfwChannel(7012))
    bot._guilds[guild.id] = guild
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild.id,
        channel_id=7012,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    session = SessionContext(
        session_id="chat-group-nsfw-broken",
        guild_id=guild.id,
        thread_id=7012,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    output = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "create", "name", "NG", "mode", "public", "channel", "7012"],
    )
    assert "nsfw not allowed" in output


@pytest.mark.asyncio
async def test_execute_chat_group_approve_does_not_mutate_other_group_apply(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild1 = FakeGuild(6101)
    guild1.add_channel(FakeTextChannel(7101))
    guild2 = FakeGuild(6102)
    guild2.add_channel(FakeTextChannel(7102))
    guild3 = FakeGuild(6103)
    guild3.add_channel(FakeTextChannel(7103))
    bot._guilds[guild1.id] = guild1
    bot._guilds[guild2.id] = guild2
    bot._guilds[guild3.id] = guild3

    cog = CliCog(bot)
    await cog.cog_load()

    leader_ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild1.id,
        channel_id=7101,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    leader_session = SessionContext(
        session_id="leader-approve",
        guild_id=guild1.id,
        thread_id=7101,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild1.id,
    )
    created1 = await cog._execute_utils(
        leader_ctx,
        leader_session,
        ["chat-group", "create", "name", "A", "mode", "private", "channel", "7101"],
    )
    group_a = created1.split("group-id=", 1)[1].strip()
    created2 = await cog._execute_utils(
        leader_ctx,
        leader_session,
        ["chat-group", "create", "name", "B", "mode", "private", "channel", "7101"],
    )
    group_b = created2.split("group-id=", 1)[1].strip()

    member_ctx = EngineContext(
        actor_user_id=200,
        guild_id=guild2.id,
        channel_id=7102,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    member_session = SessionContext(
        session_id="member-apply",
        guild_id=guild2.id,
        thread_id=7102,
        actor_user_id=200,
        scope_type=ScopeType.GUILD,
        scope_id=guild2.id,
    )
    apply_out = await cog._execute_utils(
        member_ctx,
        member_session,
        ["chat-group", "join", group_b, "channel", "7102"],
    )
    assert "apply-id=" in apply_out
    apply_id = apply_out.split("apply-id=", 1)[1].split()[0]

    bad_approve = await cog._execute_utils(
        leader_ctx,
        leader_session,
        ["chat-group", "manage-group", group_a, "approve", apply_id],
    )
    assert "reason=not found" in bad_approve

    pending_b = await cog.storage.list_chat_group_applications(group_b, status="pending")
    assert any(row.apply_id == apply_id for row in pending_b)


@pytest.mark.asyncio
async def test_execute_chat_group_approve_rejects_non_pending_apply(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild1 = FakeGuild(6201)
    guild1.add_channel(FakeTextChannel(7201))
    guild2 = FakeGuild(6202)
    guild2.add_channel(FakeTextChannel(7202))
    bot._guilds[guild1.id] = guild1
    bot._guilds[guild2.id] = guild2

    cog = CliCog(bot)
    await cog.cog_load()

    leader_ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild1.id,
        channel_id=7201,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    leader_session = SessionContext(
        session_id="leader-pending-only",
        guild_id=guild1.id,
        thread_id=7201,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild1.id,
    )
    created = await cog._execute_utils(
        leader_ctx,
        leader_session,
        ["chat-group", "create", "name", "P", "mode", "private", "channel", "7201"],
    )
    group_id = created.split("group-id=", 1)[1].strip()

    member_ctx = EngineContext(
        actor_user_id=200,
        guild_id=guild2.id,
        channel_id=7202,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    member_session = SessionContext(
        session_id="member-pending-only",
        guild_id=guild2.id,
        thread_id=7202,
        actor_user_id=200,
        scope_type=ScopeType.GUILD,
        scope_id=guild2.id,
    )
    apply_out = await cog._execute_utils(
        member_ctx,
        member_session,
        ["chat-group", "join", group_id, "channel", "7202"],
    )
    apply_id = apply_out.split("apply-id=", 1)[1].split()[0]

    approve_1 = await cog._execute_utils(
        leader_ctx,
        leader_session,
        ["chat-group", "manage-group", group_id, "approve", apply_id],
    )
    assert "ok approve apply-id=" in approve_1

    approve_2 = await cog._execute_utils(
        leader_ctx,
        leader_session,
        ["chat-group", "manage-group", group_id, "approve", apply_id],
    )
    assert "reason=invalid state" in approve_2


@pytest.mark.asyncio
async def test_chat_group_sync_keeps_cli_select_id_stable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild = FakeGuild(6301)
    guild.add_channel(FakeTextChannel(7301))
    bot._guilds[guild.id] = guild

    cog = CliCog(bot)
    await cog.cog_load()

    ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild.id,
        channel_id=7301,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    session = SessionContext(
        session_id="sync-id-stable",
        guild_id=guild.id,
        thread_id=7301,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )

    created_a = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "create", "name", "A", "mode", "public", "channel", "7301"],
    )
    group_a = created_a.split("group-id=", 1)[1].strip()
    await cog._sync_chat_group_config_for_guilds([guild.id])
    row = await cog.storage.load_config("guild", guild.id, "chat-group")
    groups = row.data["payload"]["startup_payload"]["groups"]
    id_a_before = next(int(item["id"]) for item in groups if item["group_id"] == group_a)

    created_b = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "create", "name", "B", "mode", "public", "channel", "7301"],
    )
    group_b = created_b.split("group-id=", 1)[1].strip()
    await cog._sync_chat_group_config_for_guilds([guild.id])
    row = await cog.storage.load_config("guild", guild.id, "chat-group")
    groups_after = row.data["payload"]["startup_payload"]["groups"]
    id_a_after = next(int(item["id"]) for item in groups_after if item["group_id"] == group_a)
    id_b_after = next(int(item["id"]) for item in groups_after if item["group_id"] == group_b)

    assert id_a_after == id_a_before
    assert id_b_after > id_a_after


@pytest.mark.asyncio
async def test_chat_group_resolve_channel_fetch_failure_writes_system_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild = FakeGuildFetchFails(6003)
    bot._guilds[guild.id] = guild
    cog = CliCog(bot)
    await cog.cog_load()

    channel, error = await cog._resolve_channel_for_guild(guild.id, 777777)
    assert channel is None
    assert "field=channel" in error
    logs = await cog.storage.fetch_logs("system", guild.id, 20)
    assert any(log.section == "chat-group" and log.result == "channel-resolve-failed" for log in logs)


@pytest.mark.asyncio
async def test_chat_group_resolve_channel_fetch_failure_survives_log_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild = FakeGuildFetchFails(6004)
    bot._guilds[guild.id] = guild
    cog = CliCog(bot)
    await cog.cog_load()

    async def _raise(*args, **kwargs):
        raise RuntimeError("system log unavailable")

    monkeypatch.setattr(cog.storage, "insert_system_log", _raise)

    channel, error = await cog._resolve_channel_for_guild(guild.id, 888888)
    assert channel is None
    assert "field=channel reason=not found" in error


@pytest.mark.asyncio
async def test_execute_chat_group_message_delete_logs_target_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild = FakeGuild(6401)
    guild.add_channel(FakeTextChannel(7401))
    bot._guilds[guild.id] = guild

    cog = CliCog(bot)
    await cog.cog_load()

    ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild.id,
        channel_id=7401,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    session = SessionContext(
        session_id="delete-log-failure",
        guild_id=guild.id,
        thread_id=7401,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    created = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "create", "name", "D", "mode", "public", "channel", "7401"],
    )
    group_id = created.split("group-id=", 1)[1].strip()

    message_id = await cog.storage.insert_chat_group_message(
        group_id=group_id,
        source_guild_id=guild.id,
        source_channel_id=7401,
        source_message_id=9001,
        author_user_id=100,
        author_name="tester",
        content="x",
        attachment_urls=[],
    )
    await cog.storage.insert_chat_group_delivery(
        message_id=message_id,
        group_id=group_id,
        target_guild_id=guild.id,
        target_channel_id=7410,
        target_message_id=9010,
        status="ok",
        error=None,
    )

    monkeypatch.setattr(cog.bot, "get_channel", lambda channel_id: FakeFetchMessageChannel(channel_id) if int(channel_id) == 7410 else None)

    result = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "message", group_id, "delete", str(message_id)],
    )
    assert "ok deleted message-id=" in result

    logs = await cog.storage.fetch_logs("system", guild.id, 20)
    assert any(log.section == "chat-group" and log.result == "message-delete-failed" for log in logs)


@pytest.mark.asyncio
async def test_execute_chat_group_message_delete_logs_channel_unavailable_and_fetch_unsupported(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild = FakeGuild(6402)
    guild.add_channel(FakeTextChannel(7402))
    bot._guilds[guild.id] = guild

    cog = CliCog(bot)
    await cog.cog_load()

    ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild.id,
        channel_id=7402,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    session = SessionContext(
        session_id="delete-log-channel-missing",
        guild_id=guild.id,
        thread_id=7402,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    created = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "create", "name", "E", "mode", "public", "channel", "7402"],
    )
    group_id = created.split("group-id=", 1)[1].strip()
    message_id = await cog.storage.insert_chat_group_message(
        group_id=group_id,
        source_guild_id=guild.id,
        source_channel_id=7402,
        source_message_id=9002,
        author_user_id=100,
        author_name="tester",
        content="x",
        attachment_urls=[],
    )
    await cog.storage.insert_chat_group_delivery(
        message_id=message_id,
        group_id=group_id,
        target_guild_id=guild.id,
        target_channel_id=7411,
        target_message_id=9011,
        status="ok",
        error=None,
    )
    await cog.storage.insert_chat_group_delivery(
        message_id=message_id,
        group_id=group_id,
        target_guild_id=guild.id,
        target_channel_id=7412,
        target_message_id=9012,
        status="ok",
        error=None,
    )

    def _get_channel(channel_id: int):
        if int(channel_id) == 7412:
            return FakeNoFetchChannel(channel_id)
        return None

    monkeypatch.setattr(cog.bot, "get_channel", _get_channel)

    result = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "message", group_id, "delete", str(message_id)],
    )
    assert "ok deleted message-id=" in result

    logs = await cog.storage.fetch_logs("system", guild.id, 50)
    failure_logs = [log for log in logs if log.section == "chat-group" and log.result == "message-delete-failed"]
    assert len(failure_logs) >= 2


@pytest.mark.asyncio
async def test_execute_chat_group_message_delete_survives_log_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bot = FakeBot()
    guild = FakeGuild(6403)
    guild.add_channel(FakeTextChannel(7403))
    bot._guilds[guild.id] = guild

    cog = CliCog(bot)
    await cog.cog_load()

    ctx = EngineContext(
        actor_user_id=100,
        guild_id=guild.id,
        channel_id=7403,
        is_bot_admin=False,
        has_manage_guild=True,
    )
    session = SessionContext(
        session_id="delete-log-fail-safe",
        guild_id=guild.id,
        thread_id=7403,
        actor_user_id=100,
        scope_type=ScopeType.GUILD,
        scope_id=guild.id,
    )
    created = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "create", "name", "F", "mode", "public", "channel", "7403"],
    )
    group_id = created.split("group-id=", 1)[1].strip()
    message_id = await cog.storage.insert_chat_group_message(
        group_id=group_id,
        source_guild_id=guild.id,
        source_channel_id=7403,
        source_message_id=9003,
        author_user_id=100,
        author_name="tester",
        content="x",
        attachment_urls=[],
    )
    await cog.storage.insert_chat_group_delivery(
        message_id=message_id,
        group_id=group_id,
        target_guild_id=guild.id,
        target_channel_id=7413,
        target_message_id=9013,
        status="ok",
        error=None,
    )

    monkeypatch.setattr(cog.bot, "get_channel", lambda _channel_id: None)

    async def _raise(*args, **kwargs):
        raise RuntimeError("system log unavailable")

    monkeypatch.setattr(cog.storage, "insert_system_log", _raise)

    result = await cog._execute_utils(
        ctx,
        session,
        ["chat-group", "message", group_id, "delete", str(message_id)],
    )
    assert "ok deleted message-id=" in result


@pytest.mark.asyncio
async def test_cli_no_message_response_reaction_failure_writes_system_log(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("cogs.cli.discord.Member", FakeMemberBase)

    guild = FakeGuild(9003)
    author = FakeMember(user_id=7778, display_name="logger2", manage_guild=True)
    thread = FakeThread(thread_id=9004)
    incoming = [
        FakeIncomingMessage(author=author, channel=thread, content="execute cli to-file start no-message-response"),
        FakeIncomingMessageReactionFail(author=author, channel=thread, content="where"),
        FakeIncomingMessage(author=author, channel=thread, content="quit"),
    ]
    bot = FakeBot(incoming=incoming)
    cog = CliCog(bot)
    await cog.cog_load()

    ctx = FakeContext(guild=guild, author=author, message=FakeCommandMessage(thread=thread), channel=thread)
    await invoke_cli(cog, ctx)

    logs = await cog.storage.fetch_logs("system", guild.id, 20)
    assert any(log.section == "cli" and log.result == "reaction-add-failed" for log in logs)
