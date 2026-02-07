from __future__ import annotations

import pytest

from utils.cli.engine import CliEngine
from utils.cli.types import EngineContext, ScopeType
from utils.storage import Storage


@pytest.fixture()
def guild_ctx() -> EngineContext:
    return EngineContext(
        actor_user_id=100,
        guild_id=12345,
        channel_id=555,
        is_bot_admin=False,
        has_manage_guild=True,
    )


@pytest.fixture()
def admin_ctx() -> EngineContext:
    return EngineContext(
        actor_user_id=999,
        guild_id=12345,
        channel_id=555,
        is_bot_admin=True,
        has_manage_guild=True,
    )


@pytest.fixture()
def notifier_events():
    return []


@pytest.fixture()
def notifier(notifier_events):
    async def _notify(channel_id: int, message: str) -> str:
        notifier_events.append((channel_id, message))
        return "sent"

    return _notify


@pytest.mark.asyncio
async def test_engine_set_and_deploy(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)

    session, result = await engine.execute(guild_ctx, session, "enter welcome")
    assert result.output == "ok"

    session, result = await engine.execute(guild_ctx, session, "set join-roles 1 2")
    assert result.output == "ok"

    session, result = await engine.execute(guild_ctx, session, "deploy")
    assert "deployed:" in result.output

    row = await storage.load_config("guild", guild_ctx.guild_id, "welcome")
    assert row is not None
    assert row.data["payload"]["join_roles"] == [1, 2]


@pytest.mark.asyncio
async def test_engine_root_enforce_blocks_guild_set(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter root-enforce")
    admin_session, result = await engine.execute(admin_ctx, admin_session, "set log-config.audit-log-max-buffer 5000")
    assert result.output == "ok"
    admin_session, result = await engine.execute(admin_ctx, admin_session, "deploy")
    assert "deployed:" in result.output

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter log-config")
    guild_session, result = await engine.execute(guild_ctx, guild_session, "set audit-log-max-buffer 6000")
    assert "reason=enforced by root" in result.output


@pytest.mark.asyncio
async def test_engine_get_logs(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, 'set welcome-message "hello"')

    session, result = await engine.execute(guild_ctx, session, "get log audit")
    assert "audit logs" in result.output

    session, result = await engine.execute(guild_ctx, session, "get log system")
    assert "system logs" in result.output


@pytest.mark.asyncio
async def test_get_counters_all_counts_ok_and_error(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 1 2")
    session, _ = await engine.execute(guild_ctx, session, "set unknown-key x")

    session, result = await engine.execute(guild_ctx, session, "get counters all")
    assert "section=welcome command=set" in result.output
    assert "error=" in result.output


@pytest.mark.asyncio
async def test_crash_generates_error_id_and_persists_traceback(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(engine, "_cmd_where", boom)

    session, result = await engine.execute(guild_ctx, session, "where")
    assert "fatal error: error_id=CR-" in result.output
    error_id = result.output.split("error_id=")[1]

    crash_rows = await storage.fetch_crash_logs(scope_type="guild", scope_id=guild_ctx.guild_id, limit=10)
    assert len(crash_rows) == 1
    assert crash_rows[0].message == "boom"
    assert "RuntimeError: boom" in crash_rows[0].traceback

    session, result = await engine.execute(guild_ctx, session, "get log crash")
    assert "scope=guild:12345" in result.output
    assert "path=(top)" in result.output
    assert "args=[]" in result.output

    session, result = await engine.execute(guild_ctx, session, f"get log crash {error_id}")
    assert f"error_id={error_id}" in result.output
    assert "traceback:" in result.output
    assert "RuntimeError: boom" in result.output


@pytest.mark.asyncio
async def test_get_log_crash_scope_filter(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("crash-a")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    session, _ = await engine.execute(guild_ctx, session, "where")

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, result = await engine.execute(admin_ctx, admin_session, "get log crash")
    assert "(empty)" in result.output


@pytest.mark.asyncio
async def test_send_crashlog_root_disabled_no_forward(monkeypatch, tmp_path, guild_ctx: EngineContext, notifier):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage, crash_notifier=notifier)

    session, _ = await engine.initialize_session(guild_ctx)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("disabled")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    session, _ = await engine.execute(guild_ctx, session, "where")

    root_rows = await storage.fetch_crash_logs(scope_type="root", scope_id=guild_ctx.guild_id, limit=10)
    assert root_rows == []


@pytest.mark.asyncio
async def test_receive_mode_off_drops(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext, notifier):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage, crash_notifier=notifier)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter tenant-connection")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter log")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set receive-mode off")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter control-plane")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter root-connection")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "set send-crashlog-root enable")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "deploy")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("drop")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "where")

    root_rows = await storage.fetch_crash_logs(scope_type="root", scope_id=guild_ctx.guild_id, limit=10)
    assert root_rows == []


@pytest.mark.asyncio
async def test_receive_mode_database_saves_root_copy(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext, notifier):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage, crash_notifier=notifier)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter tenant-connection")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter log")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set receive-mode database")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter control-plane")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter root-connection")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "set send-crashlog-root enable")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "deploy")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("to-db")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "where")

    root_rows = await storage.fetch_crash_logs(scope_type="root", scope_id=guild_ctx.guild_id, limit=10)
    assert len(root_rows) >= 1


@pytest.mark.asyncio
async def test_receive_mode_discord_requires_channel(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext, notifier_events, notifier):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage, crash_notifier=notifier)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter tenant-connection")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter log")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set receive-mode discord")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter control-plane")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter root-connection")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "set send-crashlog-root enable")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "deploy")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("discord-drop")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "where")

    assert notifier_events == []


@pytest.mark.asyncio
async def test_receive_mode_both_with_channel(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext, notifier_events, notifier):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage, crash_notifier=notifier)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter tenant-connection")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter log")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set receive-mode both")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set crashlog-report-channel 777")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter control-plane")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter root-connection")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "set send-crashlog-root enable")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "deploy")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("both")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "where")

    root_rows = await storage.fetch_crash_logs(scope_type="root", scope_id=guild_ctx.guild_id, limit=10)
    assert len(root_rows) >= 1
    assert len(notifier_events) == 1
    assert notifier_events[0][0] == 777


@pytest.mark.asyncio
async def test_receive_mode_alias_recive_mode(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter tenant-connection")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter log")
    admin_session, result = await engine.execute(admin_ctx, admin_session, "set recive-mode database")
    assert result.output == "ok"


@pytest.mark.asyncio
async def test_crash_log_trim_to_500(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    for index in range(600):
        await storage.insert_crash_log(
            scope_type="guild",
            scope_id=1,
            actor_user_id=1,
            section="global",
            command="x",
            message=str(index),
            traceback_text="tb",
            context_json={"n": index},
            forward_mode="off",
            forward_status="not-forwarded",
        )
    await storage.trim_crash_logs("guild", 1, 500)
    rows = await storage.fetch_crash_logs("guild", 1, 1000)
    assert len(rows) == 500
