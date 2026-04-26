from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.cli.engine import CliEngine
from utils.cli.types import EngineContext, ScopeType
from utils.guild_log_cache import CachedMessage, guild_message_cache
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
    assert "deployed startup:" in result.output

    row = await storage.load_config("guild", guild_ctx.guild_id, "welcome")
    assert row is not None
    assert row.data["payload"]["running_payload"]["join_roles"] == [1, 2]
    assert row.data["payload"]["startup_payload"]["join_roles"] == [1, 2]


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
    assert "deployed startup:" in result.output

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
async def test_engine_get_logs_survives_fetch_failure(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    async def _raise_logs(*args, **kwargs):
        raise RuntimeError("db unavailable")

    async def _raise_crash_list(*args, **kwargs):
        raise RuntimeError("db unavailable")

    async def _raise_crash_one(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(storage, "fetch_logs", _raise_logs)
    monkeypatch.setattr(storage, "fetch_crash_logs", _raise_crash_list)
    monkeypatch.setattr(storage, "fetch_crash_log_by_error_id", _raise_crash_one)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "get log system")
    assert "system logs" in result.output
    assert "(empty)" in result.output

    session, result = await engine.execute(guild_ctx, session, "get log crash")
    assert "crash logs" in result.output
    assert "(empty)" in result.output

    session, result = await engine.execute(guild_ctx, session, "get log crash CR-abc")
    assert result.output == "crash log not found: error_id=CR-abc"


@pytest.mark.asyncio
async def test_get_log_audit_summarizes_multiline_result(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "show")
    session, result = await engine.execute(guild_ctx, session, "get log audit 20")

    lines = result.output.splitlines()
    assert lines[0].startswith("audit logs")
    assert all(line.startswith("[") for line in lines[1:] if line != "(empty)")


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
async def test_get_tick_status(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "where")
    session, result = await engine.execute(guild_ctx, session, "get tick status")
    assert "tick status guild=" in result.output
    assert "categories:" in result.output
    assert "category=command" in result.output
    assert "sources:" in result.output


@pytest.mark.asyncio
async def test_diagnose_config_validate_defaults_to_now_config(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter management-module")
    session, _ = await engine.execute(guild_ctx, session, "set level disable")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, _ = await engine.execute(guild_ctx, session, "set max-level 120")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "diagnose config validate")
    assert "target=now-config" in result.output
    assert "warn=" in result.output
    assert "module-disabled" in result.output


@pytest.mark.asyncio
async def test_diagnose_config_validate_supports_deploy_config(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "diagnose config validate deploy-config")
    assert "target=deploy-config" in result.output


@pytest.mark.asyncio
async def test_diagnose_config_validate_rejects_invalid_target(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "diagnose config validate invalid")
    assert "field=diagnose reason=invalid args" in result.output


@pytest.mark.asyncio
async def test_control_plane_tick_set_requires_bot_admin(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter control-plane")
    session, _ = await engine.execute(guild_ctx, session, "enter tick")
    session, result = await engine.execute(guild_ctx, session, "set max-tick-limit 6000")
    assert "field=tick reason=forbidden" in result.output

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter control-plane")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter tick")
    admin_session, result = await engine.execute(admin_ctx, admin_session, "set max-tick-limit 6000")
    assert result.output == "ok"


@pytest.mark.asyncio
async def test_control_plane_timezone_set_and_show(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter control-plane")
    session, result = await engine.execute(guild_ctx, session, "set timezone Asia/Tokyo")
    assert result.output == "ok"

    session, result = await engine.execute(guild_ctx, session, "show now-config")
    assert "set timezone Asia/Tokyo" in result.output


@pytest.mark.asyncio
async def test_get_log_uses_timezone_resolution(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    guild_ctx.guild = SimpleNamespace(voice_channels=[SimpleNamespace(rtc_region="japan")], preferred_locale="en-US")

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, 'set welcome-message "hello"')
    session, result = await engine.execute(guild_ctx, session, "get log audit")
    assert "local=" in result.output
    assert "utc=" in result.output
    assert "Asia/Tokyo" in result.output


@pytest.mark.asyncio
async def test_timezone_unresolved_warning_log_failure_does_not_break_show(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    guild_ctx.guild = SimpleNamespace(voice_channels=[SimpleNamespace(rtc_region="unknown-region")], preferred_locale="en-US")

    async def _raise(*args, **kwargs):
        raise RuntimeError("system log unavailable")

    monkeypatch.setattr(storage, "insert_system_log", _raise)
    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "get log audit")
    assert "audit logs" in result.output
    assert "(empty)" in result.output


@pytest.mark.asyncio
async def test_root_tenant_connection_running_only_log_payload_is_compatible(
    monkeypatch, tmp_path, admin_ctx: EngineContext
):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    await storage.upsert_config(
        "root",
        0,
        "tenant-connection",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "log": {"receive_mode": "database", "crashlog_report_channel": 777, "crashlog_max_buffer": 900},
                }
            },
        },
    )

    session, _ = await engine.initialize_session(admin_ctx)
    session, result = await engine.execute(admin_ctx, session, "switch root")
    assert result.output == "ok"
    session, result = await engine.execute(admin_ctx, session, "enter tenant-connection")
    assert result.output == "ok"
    session, result = await engine.execute(admin_ctx, session, "enter log")
    assert result.output == "ok"
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "set receive-mode database" in result.output
    assert "set crashlog-report-channel 777" in result.output

    row = await storage.load_config("root", 0, "tenant-connection")
    assert row is not None
    assert "startup_payload" in row.data["payload"]


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
async def test_crash_returns_error_id_even_when_crashlog_persist_fails(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom-safe")

    async def fail_safe_insert(**kwargs):
        return False, kwargs.get("error_id", "CR-fallback")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    monkeypatch.setattr(storage, "insert_crash_log_safe", fail_safe_insert)

    session, result = await engine.execute(guild_ctx, session, "where")
    assert "fatal error: error_id=CR-" in result.output
    crash_rows = await storage.fetch_crash_logs(scope_type="guild", scope_id=guild_ctx.guild_id, limit=10)
    assert crash_rows == []


@pytest.mark.asyncio
async def test_crash_trim_failure_records_resilience_counter(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    async def boom(*_args, **_kwargs):
        raise RuntimeError("boom-trim")

    async def _trim_raise(*_args, **_kwargs):
        raise RuntimeError("trim unavailable")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    monkeypatch.setattr(storage, "trim_crash_logs", _trim_raise)

    session, result = await engine.execute(guild_ctx, session, "where")
    assert "fatal error: error_id=CR-" in result.output
    session, counters = await engine.execute(guild_ctx, session, "get counters all")
    assert "section=resilience command=crash-local-trim" in counters.output


@pytest.mark.asyncio
async def test_crash_root_copy_failure_records_resilience_counter(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

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
        raise RuntimeError("boom-root-copy")

    async def _copy_raise(*_args, **_kwargs):
        raise RuntimeError("copy unavailable")

    monkeypatch.setattr(engine, "_cmd_where", boom)
    monkeypatch.setattr(storage, "insert_root_crash_copy", _copy_raise)

    guild_session, result = await engine.execute(guild_ctx, guild_session, "where")
    assert "fatal error: error_id=CR-" in result.output
    guild_session, counters = await engine.execute(guild_ctx, guild_session, "get counters all")
    assert "section=resilience command=crash-root-copy" in counters.output


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


@pytest.mark.asyncio
async def test_enter_question_lists_sections(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "enter ?")
    assert "candidates:" in result.output
    assert "welcome" in result.output
    assert "console" in result.output


@pytest.mark.asyncio
async def test_enter_prefix_question_filters_sections(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "enter w?")
    assert "welcome" in result.output
    assert "log-config" not in result.output


@pytest.mark.asyncio
async def test_set_question_lists_keys_in_section(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, result = await engine.execute(guild_ctx, session, "set ?")
    assert "join-roles" in result.output
    assert "welcome-message" in result.output


@pytest.mark.asyncio
async def test_set_prefix_question_filters_keys(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, result = await engine.execute(guild_ctx, session, "set w?")
    assert "welcome-message" in result.output
    assert "join-roles" not in result.output


@pytest.mark.asyncio
async def test_set_key_question_lists_value_candidates(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter log-config")
    session, result = await engine.execute(guild_ctx, session, "set audit-log-max-buffer ?")
    assert "<100..100000>" in result.output


@pytest.mark.asyncio
async def test_console_always_print_help_enable_appends_candidates(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter console")
    session, _ = await engine.execute(guild_ctx, session, "set always-print-help enable")
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "where")
    assert "next candidates:" in result.output
    assert "enter welcome" in result.output


@pytest.mark.asyncio
async def test_console_always_print_help_disable_no_append(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter console")
    session, _ = await engine.execute(guild_ctx, session, "set always-print-help disable")
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "where")
    assert "next candidates:" not in result.output


@pytest.mark.asyncio
async def test_always_print_help_applies_on_error_response(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter console")
    session, _ = await engine.execute(guild_ctx, session, "set always-print-help enable")
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    session, result = await engine.execute(guild_ctx, session, "set unknown x")
    assert "reason=unknown key" in result.output
    assert "next candidates:" in result.output


@pytest.mark.asyncio
async def test_console_setting_persisted_across_session(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter console")
    session, _ = await engine.execute(guild_ctx, session, "set always-print-help enable")
    session, _ = await engine.execute(guild_ctx, session, "deploy")

    new_session, _ = await engine.initialize_session(guild_ctx)
    new_session, result = await engine.execute(guild_ctx, new_session, "where")
    assert "next candidates:" in result.output


@pytest.mark.asyncio
async def test_show_global_returns_now_config_only(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, 'set welcome-message "hello"')
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show")

    assert result.output.startswith("now-config:\n")
    assert "enter root-enforce\n  # no settings" in result.output
    assert "enter welcome\n  set welcome-message hello" in result.output
    assert "deploy-config:" not in result.output
    assert "section=" not in result.output
    assert "committed:" not in result.output


@pytest.mark.asyncio
async def test_show_now_config_global_only_now_blocks(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, 'set welcome-message "hello"')
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show now-config")

    assert result.output.startswith("now-config:\n")
    assert "enter welcome\n  set welcome-message hello" in result.output
    assert "deploy-config:" not in result.output


@pytest.mark.asyncio
async def test_show_deploy_config_global_only_deploy_blocks(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 1")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show deploy-config")

    assert result.output.startswith("deploy-config:\n")
    assert "enter welcome\n  # no settings" in result.output
    assert "now-config:\nenter welcome" not in result.output


@pytest.mark.asyncio
async def test_show_diff_config_global_renders_compare_blocks(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 1")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show diff-config")

    assert result.output.startswith("diff-config:\n")
    assert "now-config:" in result.output
    assert "deploy-config:" in result.output
    assert "enter welcome" in result.output


@pytest.mark.asyncio
async def test_show_diff_config_diff_only_global_shows_changed_sections_only(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 1")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show diff-config diff-only")

    assert result.output.startswith("diff-config:\n")
    assert "enter welcome" in result.output
    assert "enter log-config" not in result.output


@pytest.mark.asyncio
async def test_show_diff_config_diff_only_section_returns_no_differences(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, result = await engine.execute(guild_ctx, session, "show diff-config diff-only")

    assert result.output == "# no differences"


@pytest.mark.asyncio
async def test_show_global_displays_none_when_no_candidate(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "show")

    assert result.output.startswith("now-config:\n")
    assert "enter welcome" in result.output
    assert "enter guild-log\n  enter message-log" in result.output
    assert "  enter member-log" in result.output
    assert "set audit-log-max-buffer 10000" in result.output
    assert "set gain-time 10" in result.output


@pytest.mark.asyncio
async def test_show_global_has_no_duplicate_top_level_enter_sections(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "show")

    top_enters: list[str] = []
    for line in result.output.splitlines():
        if line.startswith("enter "):
            top_enters.append(line)
    assert len(top_enters) == len(set(top_enters))


@pytest.mark.asyncio
async def test_show_global_expands_guild_log_subsections(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter guild-log")
    session, _ = await engine.execute(guild_ctx, session, "enter mod-log")
    session, _ = await engine.execute(guild_ctx, session, "set type ban kick")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show")

    assert "enter guild-log\n  enter message-log\n    set tracking-message-count 1000" in result.output
    assert "    set tracking-message-mode normal" in result.output
    assert "  enter mod-log\n    set type ban kick" in result.output
    assert "  enter message-log" in result.output
    assert "  enter member-log" in result.output
    assert "section=" not in result.output


@pytest.mark.asyncio
async def test_show_global_root_scope_guild_only_message(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, result = await engine.execute(admin_ctx, session, "show")
    assert result.output == "show(global) is guild-only"


@pytest.mark.asyncio
async def test_show_section_mode_regression(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 1 2")
    session, result = await engine.execute(guild_ctx, session, "show")

    assert result.output.startswith("enter welcome\n")
    assert "deploy-config:" not in result.output


@pytest.mark.asyncio
async def test_show_now_config_in_section_only_current_section(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, _ = await engine.execute(guild_ctx, session, "set gain-time 10")
    session, result = await engine.execute(guild_ctx, session, "show now-config")

    assert result.output.startswith("enter level-common\n")
    assert "set gain-time 10" in result.output
    assert "deploy-config:" not in result.output


@pytest.mark.asyncio
async def test_show_deploy_config_in_section_only_current_section(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, _ = await engine.execute(guild_ctx, session, "set gain-time 10")
    session, result = await engine.execute(guild_ctx, session, "show deploy-config")

    assert result.output.startswith("enter level-common\n")
    assert "set gain-time 10" in result.output
    assert "now-config:" not in result.output


@pytest.mark.asyncio
async def test_show_diff_config_in_section_renders_compare(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, _ = await engine.execute(guild_ctx, session, "set gain-time 10")
    session, result = await engine.execute(guild_ctx, session, "show diff-config")

    assert "now-config:" in result.output
    assert "deploy-config:" in result.output
    assert "enter level-common" in result.output


@pytest.mark.asyncio
async def test_show_invalid_arg_rejected(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "show broken-mode")
    assert "field=show reason=invalid args" in result.output


@pytest.mark.asyncio
async def test_show_global_uses_enter_path_for_nested_sections(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter guild-log")
    session, _ = await engine.execute(guild_ctx, session, "enter mod-log")
    session, _ = await engine.execute(guild_ctx, session, "set type ban")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show")

    assert "enter guild-log\n  enter message-log" in result.output
    assert "  enter mod-log" in result.output
    assert "enter guild-log/mod-log" not in result.output


@pytest.mark.asyncio
async def test_show_global_uses_enter_path_for_control_plane(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter control-plane")
    session, _ = await engine.execute(guild_ctx, session, "enter root-connection")
    session, _ = await engine.execute(guild_ctx, session, "set send-crashlog-root enable")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show")

    assert "enter control-plane" in result.output
    assert "enter root-connection" in result.output
    assert "enter control-plane\n  # no settings\n  enter root-connection" in result.output
    assert "enter control-plane/root-connection" not in result.output
    assert "set send-crashlog-root enable" in result.output


@pytest.mark.asyncio
async def test_show_global_groups_sticky_message_once(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter sticky-message")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, 'set message "hello sticky"')
    session, _ = await engine.execute(guild_ctx, session, "set delay 3")
    session, _ = await engine.execute(guild_ctx, session, "enter channels")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, "set channel-id 123")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show")

    assert result.output.count("enter sticky-message") == 1
    assert 'enter sticky-message\n  select 1\n    set message "hello sticky"\n    set delay 3' in result.output
    assert "    enter channels\n      select 1\n        set channel-id 123" in result.output
    assert "        enter webhook\n          set name \"\"" in result.output
    assert "    enter embed" in result.output
    assert "        leave\n      leave\n    enter embed" in result.output
    assert result.output.count("  enter channels") == 1
    assert result.output.count("  enter embed") == 1
    assert "enter sticky-message/channels" not in result.output


@pytest.mark.asyncio
async def test_question_command_lists_candidates(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "?")
    assert "candidates:" in result.output
    assert "enter welcome" in result.output
    assert "quit" in result.output


@pytest.mark.asyncio
async def test_select_rejected_in_non_selectable_section(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, result = await engine.execute(guild_ctx, session, "select 1")
    assert "field=select reason=invalid context" in result.output


@pytest.mark.asyncio
async def test_sticky_message_select_shortcut_and_candidates(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter sticky-message")
    session, result = await engine.execute(guild_ctx, session, "?")
    assert "select <id>" in result.output
    assert "enter channels" in result.output
    assert "enter embed" in result.output

    session, result = await engine.execute(guild_ctx, session, "select 1")
    assert result.output == "selected 1"

    session, _ = await engine.execute(guild_ctx, session, "enter channels")
    session, result = await engine.execute(guild_ctx, session, "show now-config")
    assert "select 1" in result.output


@pytest.mark.asyncio
async def test_select_creates_missing_policy_id(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, result = await engine.execute(guild_ctx, session, "select 1")
    assert result.output == "selected 1"
    session, show_result = await engine.execute(guild_ctx, session, "show now-config")
    assert "select 1" in show_result.output
    assert "select 0" in show_result.output


@pytest.mark.asyncio
async def test_select_question_lists_policy_candidates(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, result = await engine.execute(guild_ctx, session, "select ?")
    assert "candidates:" in result.output
    assert "\n0" in result.output or result.output.endswith("0")


@pytest.mark.asyncio
async def test_select_question_in_non_selectable_section(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, result = await engine.execute(guild_ctx, session, "select ?")
    assert "field=select reason=invalid context" in result.output


@pytest.mark.asyncio
async def test_get_counters_missing_subcommand_returns_error(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "get counters")
    assert "field=get reason=invalid args" in result.output


@pytest.mark.asyncio
async def test_get_log_invalid_limit_returns_error(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "get log audit abc")
    assert "field=limit reason=invalid integer" in result.output


@pytest.mark.asyncio
async def test_show_log_type_uses_feature_keys(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter log-type")
    session, _ = await engine.execute(guild_ctx, session, "set welcome warn")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "set welcome warn" in result.output
    assert "levels.welcome" not in result.output


@pytest.mark.asyncio
async def test_log_type_question_candidates_hide_alias_and_root_sections_in_guild(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter log-type")
    session, result = await engine.execute(guild_ctx, session, "?")

    assert "set welcome" in result.output
    assert "set root-enforce" not in result.output
    assert "set root-defaults" not in result.output
    assert "set root-enforce-override" not in result.output
    assert "set mod-log" not in result.output
    assert "set message-log" not in result.output
    assert "set member-log" not in result.output
    assert "set control-plane/root-connection" not in result.output
    assert "set sticky-message/channels" not in result.output
    for line in result.output.splitlines():
        if line.startswith("set "):
            assert "/" not in line


@pytest.mark.asyncio
async def test_log_type_rejects_nested_feature_key(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter log-type")
    session, result = await engine.execute(guild_ctx, session, "set control-plane/root-connection warn")
    assert "reason=unknown feature" in result.output


@pytest.mark.asyncio
async def test_sticky_message_webhook_section_uses_selected_channel_context(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter sticky-message")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, "enter channels")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, "set channel-id 123")
    session, _ = await engine.execute(guild_ctx, session, "set send-mode webhook")
    session, _ = await engine.execute(guild_ctx, session, "enter webhook")
    session, result = await engine.execute(guild_ctx, session, "set webhook wh-abc")
    assert result.output == "ok"

    session, result = await engine.execute(guild_ctx, session, "show now-config")
    assert 'set webhook "wh-abc"' in result.output


@pytest.mark.asyncio
async def test_show_global_level_method_uses_gain_range_command(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-method-message")
    session, _ = await engine.execute(guild_ctx, session, "set gain-range min 5 max 10")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "set gain-range min 5 max 10" in result.output
    assert "gain-range-min" not in result.output
    assert "gain-range-max" not in result.output


@pytest.mark.asyncio
async def test_set_updates_now_config_only(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 1 2")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "enter welcome\n  set join-roles 1 2" in result.output
    assert "deploy-config:" not in result.output


@pytest.mark.asyncio
async def test_deploy_copies_now_to_deploy_config(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 5")
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    session, result = await engine.execute(guild_ctx, session, "show deploy-config")
    assert "enter welcome\n  set join-roles 5" in result.output


@pytest.mark.asyncio
async def test_discard_restores_now_from_deploy_config(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 9")
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 99")
    session, _ = await engine.execute(guild_ctx, session, "discard")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "enter welcome\n  set join-roles 9" in result.output


@pytest.mark.asyncio
async def test_boot_loads_startup_into_now(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 7")
    session, _ = await engine.execute(guild_ctx, session, "deploy")

    new_engine = CliEngine(storage)
    new_session, _ = await new_engine.initialize_session(guild_ctx)
    new_session, _ = await new_engine.execute(guild_ctx, new_session, "enter welcome")
    new_session, result = await new_engine.execute(guild_ctx, new_session, "show")
    assert "enter welcome\n  set join-roles 7" in result.output


@pytest.mark.asyncio
async def test_legacy_payload_migrates_to_running_startup(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        guild_ctx.guild_id,
        "welcome",
        {"schema_version": 1, "payload": {"join_roles": [12], "welcome_message": "legacy"}},
    )
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "enter welcome\n  set join-roles 12\n  set welcome-message legacy" in result.output


@pytest.mark.asyncio
async def test_enter_management_module_guild_only(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, result = await engine.execute(guild_ctx, guild_session, "enter management-module")
    assert result.output == "ok"

    root_session, _ = await engine.initialize_session(admin_ctx)
    root_session, _ = await engine.execute(admin_ctx, root_session, "switch root")
    root_session, result = await engine.execute(admin_ctx, root_session, "enter management-module")
    assert "reason=forbidden" in result.output


@pytest.mark.asyncio
async def test_management_module_set_welcome_enable_disable(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter management-module")
    session, _ = await engine.execute(guild_ctx, session, "set welcome disable")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "set welcome disable" in result.output
    assert "set level disable" in result.output

    session, _ = await engine.execute(guild_ctx, session, "set welcome enable")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "set welcome enable" in result.output


@pytest.mark.asyncio
async def test_management_module_unset_restores_enable_default(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter management-module")
    session, _ = await engine.execute(guild_ctx, session, "set welcome disable")
    session, _ = await engine.execute(guild_ctx, session, "unset welcome")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "set welcome enable" in result.output


@pytest.mark.asyncio
async def test_management_module_show_now_and_deploy_labels(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter management-module")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert result.output.startswith("enter management-module\n")
    assert "deploy-config:" not in result.output


@pytest.mark.asyncio
async def test_global_show_includes_management_module(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "enter management-module" in result.output


@pytest.mark.asyncio
async def test_level_policy_insert_before_shifts_ids(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, 'set name "rule-1"')
    session, _ = await engine.execute(guild_ctx, session, "insert 1")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "select 1" in result.output
    assert "select 2" in result.output
    assert "select 0" in result.output


@pytest.mark.asyncio
async def test_level_policy_move_before(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, 'set name "one"')
    session, _ = await engine.execute(guild_ctx, session, "select 2")
    session, _ = await engine.execute(guild_ctx, session, 'set name "two"')
    session, _ = await engine.execute(guild_ctx, session, "move 2 before 1")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert 'select 1\n    set name "two"' in result.output
    assert 'select 2\n    set name "one"' in result.output


@pytest.mark.asyncio
async def test_level_policy_move_after(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, 'set name "one"')
    session, _ = await engine.execute(guild_ctx, session, "select 2")
    session, _ = await engine.execute(guild_ctx, session, 'set name "two"')
    session, _ = await engine.execute(guild_ctx, session, "move 1 after 2")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert 'select 1\n    set name "two"' in result.output
    assert 'select 2\n    set name "one"' in result.output


@pytest.mark.asyncio
async def test_level_policy_move_top_bottom(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, 'set name "one"')
    session, _ = await engine.execute(guild_ctx, session, "select 2")
    session, _ = await engine.execute(guild_ctx, session, 'set name "two"')
    session, _ = await engine.execute(guild_ctx, session, "move 2 top")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert 'select 1\n    set name "two"' in result.output
    session, _ = await engine.execute(guild_ctx, session, "move 1 bottom")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert 'select 2\n    set name "two"' in result.output


@pytest.mark.asyncio
async def test_level_policy_move_rejects_id_zero(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, result = await engine.execute(guild_ctx, session, "move 0 top")
    assert "reason=reserved rule" in result.output


@pytest.mark.asyncio
async def test_level_policy_reorder_fills_gaps(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        guild_ctx.guild_id,
        "level-gain-policy",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "policies": [
                        {"id": 2, "name": "a", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                        {"id": 5, "name": "b", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                        {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                    ]
                },
                "startup_payload": {
                    "policies": [
                        {"id": 2, "name": "a", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                        {"id": 5, "name": "b", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                        {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                    ]
                },
            },
        },
    )
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "diagnose config level-policy reorder")
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert 'select 1\n    set name "a"' in result.output
    assert 'select 2\n    set name "b"' in result.output


@pytest.mark.asyncio
async def test_diagnose_config_level_policy_reorder_updates_now_config_only(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        guild_ctx.guild_id,
        "level-gain-policy",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "policies": [
                        {"id": 3, "name": "x", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                        {"id": 7, "name": "y", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                        {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                    ]
                },
                "startup_payload": {
                    "policies": [
                        {"id": 3, "name": "x", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                        {"id": 7, "name": "y", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                        {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
                    ]
                },
            },
        },
    )
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "diagnose config level-policy reorder")
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert 'enter level-gain-policy\n  select 1\n    set name "x"' in result.output
    session, result = await engine.execute(guild_ctx, session, "show deploy-config")
    assert 'enter level-gain-policy\n  select 3\n    set name "x"' in result.output


@pytest.mark.asyncio
async def test_discard_restores_order_from_deploy_config(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, 'set name "one"')
    session, _ = await engine.execute(guild_ctx, session, "select 2")
    session, _ = await engine.execute(guild_ctx, session, 'set name "two"')
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    session, _ = await engine.execute(guild_ctx, session, "move 2 top")
    session, _ = await engine.execute(guild_ctx, session, "discard")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert 'select 1\n    set name "one"' in result.output
    assert 'select 2\n    set name "two"' in result.output


@pytest.mark.asyncio
async def test_level_gain_policy_show_escapes_name(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, _ = await engine.execute(guild_ctx, session, 'set name "x\\"y\\\\z"')
    session, result = await engine.execute(guild_ctx, session, "show")
    assert 'set name "x\\"y\\\\z"' in result.output


@pytest.mark.asyncio
async def test_level_gain_policy_show_id0_outputs_immutable_comment(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "insert 0")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "select 0" in result.output
    assert "# immutable default rule" in result.output
    assert "select 0\nset action" not in result.output


@pytest.mark.asyncio
async def test_level_sections_available_via_enter_question(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "enter ?")
    assert "level-common" in result.output
    assert "level-method-message" in result.output
    assert "level-method-reaction" in result.output
    assert "level-method-voice" in result.output
    assert "level-shared" in result.output
    assert "level-segment-table" in result.output
    assert "level-static-table" in result.output
    assert "level-gain-policy" in result.output


@pytest.mark.asyncio
async def test_level_common_and_methods_set_show(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, _ = await engine.execute(guild_ctx, session, "set level-table segment-interpolation")
    session, _ = await engine.execute(guild_ctx, session, "set max-level 10")
    session, _ = await engine.execute(guild_ctx, session, "set gain-policy enable")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "set level-table segment-interpolation" in result.output
    assert "set max-level 10" in result.output
    assert "set gain-policy enable" in result.output

    session, _ = await engine.execute(guild_ctx, session, "top")
    session, _ = await engine.execute(guild_ctx, session, "enter level-method-message")
    session, _ = await engine.execute(guild_ctx, session, "set gain-mode random-range")
    session, _ = await engine.execute(guild_ctx, session, "set gain-range min 5 max 10")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "set gain-mode random-range" in result.output
    assert "set gain-range min 5 max 10" in result.output


@pytest.mark.asyncio
async def test_level_common_set_function_base_accepts_integer(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, result = await engine.execute(guild_ctx, session, "set function-base 100")
    assert result.output == "ok"


@pytest.mark.asyncio
async def test_level_common_set_levelup_message_show(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, result = await engine.execute(guild_ctx, session, 'set levelup-message "{mention} is now level {level}!"')
    assert result.output == "ok"
    session, result = await engine.execute(guild_ctx, session, "show now-config")
    assert 'set levelup-message "{mention} is now level {level}!"' in result.output


@pytest.mark.asyncio
async def test_level_gain_policy_time_set_and_show_hhmm(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, result = await engine.execute(guild_ctx, session, "set time 09:00 18:00")
    assert result.output == "ok"
    session, result = await engine.execute(guild_ctx, session, "show now-config")
    assert "set time 09:00 18:00" in result.output


@pytest.mark.asyncio
async def test_level_gain_policy_time_rejects_invalid_format(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter level-gain-policy")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, result = await engine.execute(guild_ctx, session, "set time 9:0 18:00")
    assert "field=time reason=invalid format" in result.output


@pytest.mark.asyncio
async def test_show_level_method_renders_gain_range_replayable(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-method-message")
    session, _ = await engine.execute(guild_ctx, session, "set gain-mode random-range")
    session, _ = await engine.execute(guild_ctx, session, "set gain-range min 5 max 10")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "set gain-range min 5 max 10" in result.output
    assert "gain-range-min" not in result.output
    assert "gain-range-max" not in result.output


@pytest.mark.asyncio
async def test_show_section_nested_path_replayable_control_plane(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter control-plane")
    session, _ = await engine.execute(guild_ctx, session, "enter root-connection")
    session, _ = await engine.execute(guild_ctx, session, "set send-crashlog-root enable")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "enter control-plane\n  enter root-connection" in result.output
    assert "enter control-plane/root-connection" not in result.output


@pytest.mark.asyncio
async def test_show_section_nested_path_replayable_guild_log(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter guild-log")
    session, _ = await engine.execute(guild_ctx, session, "enter mod-log")
    session, _ = await engine.execute(guild_ctx, session, "set type ban")
    session, result = await engine.execute(guild_ctx, session, "show")
    assert "enter guild-log\n  enter mod-log" in result.output
    assert "enter guild-log/mod-log" not in result.output


@pytest.mark.asyncio
async def test_show_parent_path_in_root_scope_tenant_connection(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter tenant-connection")
    session, _ = await engine.execute(admin_ctx, session, "enter log")
    session, _ = await engine.execute(admin_ctx, session, "set receive-mode database")
    session, _ = await engine.execute(admin_ctx, session, "top")
    session, _ = await engine.execute(admin_ctx, session, "enter tenant-connection")
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert result.output.startswith("enter tenant-connection")
    assert "enter log" in result.output
    assert "set receive-mode database" in result.output
    assert "enter tick" not in result.output
    assert "show(global) is guild-only" not in result.output


@pytest.mark.asyncio
async def test_show_now_config_includes_root_enforce_and_effective_value(
    monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext
):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter root-enforce")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set control-plane/tick.max-tick-limit 9000")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, result = await engine.execute(guild_ctx, guild_session, "show now-config")
    assert "enter root-enforce\n  enter control-plane\n    enter tick\n      set max-tick-limit 9000" in result.output
    assert "enter tick\n    set max-tick-limit 9000" in result.output


@pytest.mark.asyncio
async def test_show_deploy_config_does_not_include_root_enforce_overlay(
    monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext
):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter root-enforce")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set control-plane/tick.max-tick-limit 9000")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, result = await engine.execute(guild_ctx, guild_session, "show deploy-config")
    assert "enter root-enforce" not in result.output
    assert "set max-tick-limit 9000" not in result.output


@pytest.mark.asyncio
async def test_show_now_config_backup_excludes_root_enforce_and_enforced_sections(
    monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext
):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter root-enforce")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set control-plane/tick.max-tick-limit 9000")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set log-config.audit-log-max-buffer 5000")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, result = await engine.execute(guild_ctx, guild_session, "show now-config backup")
    assert "enter root-enforce" not in result.output
    assert "enter log-config" not in result.output
    assert "set max-tick-limit 9000" not in result.output


@pytest.mark.asyncio
async def test_show_diff_config_includes_root_enforce_overlay_for_control_plane(
    monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext
):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    admin_session, _ = await engine.initialize_session(admin_ctx)
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "switch root")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "enter root-enforce")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "set control-plane/tick.max-tick-limit 9000")
    admin_session, _ = await engine.execute(admin_ctx, admin_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter control-plane")
    guild_session, result = await engine.execute(guild_ctx, guild_session, "show diff-config")
    assert "now-config:" in result.output
    assert "deploy-config:" in result.output
    assert "set max-tick-limit 9000" in result.output


@pytest.mark.asyncio
async def test_root_enforce_blocks_nested_control_plane_tick_set(
    monkeypatch, tmp_path, admin_ctx: EngineContext
):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    root_session, _ = await engine.initialize_session(admin_ctx)
    root_session, _ = await engine.execute(admin_ctx, root_session, "switch root")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter root-enforce")
    root_session, _ = await engine.execute(admin_ctx, root_session, "set control-plane/tick.max-tick-limit 9000")
    root_session, _ = await engine.execute(admin_ctx, root_session, "deploy")

    guild_session, _ = await engine.initialize_session(admin_ctx)
    guild_session, _ = await engine.execute(admin_ctx, guild_session, "enter control-plane")
    guild_session, _ = await engine.execute(admin_ctx, guild_session, "enter tick")
    guild_session, result = await engine.execute(admin_ctx, guild_session, "set max-tick-limit 8000")
    assert "reason=enforced by root" in result.output


@pytest.mark.asyncio
async def test_root_enforce_nested_enter_tick_set_and_show(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")

    session, candidates = await engine.execute(admin_ctx, session, "?")
    assert "set max-tick-limit" in candidates.output
    assert "set overlimit-mode" in candidates.output

    session, result = await engine.execute(admin_ctx, session, "set max-tick-limit 9000")
    assert result.output == "ok"
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "enter root-enforce" in result.output
    assert "enter control-plane" in result.output
    assert "enter tick" in result.output
    assert "set max-tick-limit 9000" in result.output


@pytest.mark.asyncio
async def test_root_enforce_top_level_show_renders_sections_as_enter_tree(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce")
    session, result = await engine.execute(admin_ctx, session, "set control-plane/tick.max-tick-limit 7000")
    assert result.output == "ok"

    session, result = await engine.execute(admin_ctx, session, "show")
    assert "set sections.control-plane" not in result.output
    assert "enter root-enforce" in result.output
    assert "enter control-plane" in result.output
    assert "enter tick" in result.output
    assert "set max-tick-limit 7000" in result.output


@pytest.mark.asyncio
async def test_root_defaults_nested_enter_tick_set_and_show(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-defaults")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")

    session, candidates = await engine.execute(admin_ctx, session, "?")
    assert "set max-tick-limit" in candidates.output
    assert "set overlimit-mode" in candidates.output

    session, result = await engine.execute(admin_ctx, session, "set max-tick-limit 3000")
    assert result.output == "ok"
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "enter root-defaults" in result.output
    assert "enter control-plane" in result.output
    assert "enter tick" in result.output
    assert "set max-tick-limit 3000" in result.output


@pytest.mark.asyncio
async def test_root_defaults_top_level_show_renders_sections_as_enter_tree(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-defaults")
    session, result = await engine.execute(admin_ctx, session, "set control-plane/tick.max-tick-limit 3000")
    assert result.output == "ok"

    session, result = await engine.execute(admin_ctx, session, "show")
    assert "set sections.control-plane" not in result.output
    assert "enter root-defaults" in result.output
    assert "enter control-plane" in result.output
    assert "enter tick" in result.output
    assert "set max-tick-limit 3000" in result.output


@pytest.mark.asyncio
async def test_root_enforce_override_nested_select_and_set(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 12345")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, result = await engine.execute(admin_ctx, session, "set max-tick-limit 4200")
    assert result.output == "ok"
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "enter root-enforce-override" in result.output
    assert "enter control-plane" in result.output
    assert "enter tick" in result.output
    assert "set max-tick-limit 4200" in result.output


@pytest.mark.asyncio
async def test_root_enforce_override_show_uses_selected_guild_context(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 100")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, _ = await engine.execute(admin_ctx, session, "set max-tick-limit 4100")
    session, _ = await engine.execute(admin_ctx, session, "top")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 200")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, _ = await engine.execute(admin_ctx, session, "set max-tick-limit 4200")
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "set max-tick-limit 4200" in result.output
    assert "set max-tick-limit 4100" not in result.output


@pytest.mark.asyncio
async def test_root_enforce_override_show_without_select_lists_all(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 100")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, _ = await engine.execute(admin_ctx, session, "set max-tick-limit 4100")
    session, _ = await engine.execute(admin_ctx, session, "top")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 200")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, _ = await engine.execute(admin_ctx, session, "set max-tick-limit 4200")
    session, _ = await engine.execute(admin_ctx, session, "top")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "select 100" in result.output
    assert "set control-plane/tick.max-tick-limit 4100" in result.output
    assert "select 200" in result.output
    assert "set control-plane/tick.max-tick-limit 4200" in result.output


@pytest.mark.asyncio
async def test_root_enforce_override_show_invalid_selected_falls_back_or_empty(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 100")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, _ = await engine.execute(admin_ctx, session, "set max-tick-limit 4100")
    session.selected_object = "not-a-number"
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "# no settings" in result.output
    assert "set max-tick-limit 4100" not in result.output


@pytest.mark.asyncio
async def test_leave_clears_selected_object_context(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 100")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, _ = await engine.execute(admin_ctx, session, "set max-tick-limit 4100")
    session, _ = await engine.execute(admin_ctx, session, "leave")
    session, _ = await engine.execute(admin_ctx, session, "leave")
    session, _ = await engine.execute(admin_ctx, session, "leave")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "# no settings" in result.output
    assert "set max-tick-limit 4100" not in result.output


@pytest.mark.asyncio
async def test_switch_clears_selected_object_context(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 100")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, _ = await engine.execute(admin_ctx, session, "set max-tick-limit 4100")
    session, _ = await engine.execute(admin_ctx, session, "switch guild 123")
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "# no settings" in result.output
    assert "set max-tick-limit 4100" not in result.output


@pytest.mark.asyncio
async def test_root_override_tick_show_without_select_returns_no_settings(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "select 100")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, _ = await engine.execute(admin_ctx, session, "set max-tick-limit 4100")
    session, _ = await engine.execute(admin_ctx, session, "top")
    session, _ = await engine.execute(admin_ctx, session, "enter root-enforce-override")
    session, _ = await engine.execute(admin_ctx, session, "enter control-plane")
    session, _ = await engine.execute(admin_ctx, session, "enter tick")
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "# no settings" in result.output
    assert "set max-tick-limit 4100" not in result.output


@pytest.mark.asyncio
async def test_root_enforce_override_applies_effective_now_config(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    root_session, _ = await engine.initialize_session(admin_ctx)
    root_session, _ = await engine.execute(admin_ctx, root_session, "switch root")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter root-enforce")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter control-plane")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter tick")
    root_session, _ = await engine.execute(admin_ctx, root_session, "set max-tick-limit 3000")
    root_session, _ = await engine.execute(admin_ctx, root_session, "top")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter root-enforce-override")
    root_session, _ = await engine.execute(admin_ctx, root_session, f"select {guild_ctx.guild_id}")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter control-plane")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter tick")
    root_session, _ = await engine.execute(admin_ctx, root_session, "set max-tick-limit 4200")
    root_session, _ = await engine.execute(admin_ctx, root_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter control-plane")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter tick")
    guild_session, result = await engine.execute(guild_ctx, guild_session, "show now-config")
    assert "set max-tick-limit 4200" in result.output


@pytest.mark.asyncio
async def test_root_enforce_allows_nested_guild_section_set(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    root_session, _ = await engine.initialize_session(admin_ctx)
    root_session, _ = await engine.execute(admin_ctx, root_session, "switch root")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter root-enforce")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter guild-log")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter message-log")
    root_session, result = await engine.execute(admin_ctx, root_session, "set tracking-message-mode extra")
    assert result.output == "ok"
    root_session, _ = await engine.execute(admin_ctx, root_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter guild-log")
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter message-log")
    guild_session, result = await engine.execute(guild_ctx, guild_session, "show now-config")
    assert "set tracking-message-mode extra" in result.output


@pytest.mark.asyncio
async def test_root_defaults_allows_nested_guild_section_set(monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    root_session, _ = await engine.initialize_session(admin_ctx)
    root_session, _ = await engine.execute(admin_ctx, root_session, "switch root")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter root-defaults")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter level-common")
    root_session, result = await engine.execute(admin_ctx, root_session, "set gain-time 60")
    assert result.output == "ok"
    root_session, _ = await engine.execute(admin_ctx, root_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter level-common")
    guild_session, result = await engine.execute(guild_ctx, guild_session, "show now-config")
    assert "set gain-time 60" in result.output


@pytest.mark.asyncio
async def test_root_enforce_override_allows_nested_guild_section_set(
    monkeypatch, tmp_path, guild_ctx: EngineContext, admin_ctx: EngineContext
):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    root_session, _ = await engine.initialize_session(admin_ctx)
    root_session, _ = await engine.execute(admin_ctx, root_session, "switch root")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter root-enforce")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter log-config")
    root_session, _ = await engine.execute(admin_ctx, root_session, "set audit-log-max-buffer 5000")
    root_session, _ = await engine.execute(admin_ctx, root_session, "top")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter root-enforce-override")
    root_session, _ = await engine.execute(admin_ctx, root_session, f"select {guild_ctx.guild_id}")
    root_session, _ = await engine.execute(admin_ctx, root_session, "enter log-config")
    root_session, result = await engine.execute(admin_ctx, root_session, "set audit-log-max-buffer 7000")
    assert result.output == "ok"
    root_session, _ = await engine.execute(admin_ctx, root_session, "deploy")

    guild_session, _ = await engine.initialize_session(guild_ctx)
    guild_session, _ = await engine.execute(guild_ctx, guild_session, "enter log-config")
    guild_session, result = await engine.execute(guild_ctx, guild_session, "show now-config")
    assert "set audit-log-max-buffer 7000" in result.output


@pytest.mark.asyncio
async def test_level_table_now_config_preview(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, _ = await engine.execute(guild_ctx, session, "set level-table fixed")
    session, _ = await engine.execute(guild_ctx, session, "set fixed-step 50")
    session, _ = await engine.execute(guild_ctx, session, "set max-level 3")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, result = await engine.execute(guild_ctx, session, "get level-table now-config")
    assert "level table (now-config):" in result.output
    assert "level=1 required_total_xp=50 delta_xp=50 segment=fixed" in result.output
    assert "level=3 required_total_xp=150 delta_xp=50 segment=fixed" in result.output


@pytest.mark.asyncio
async def test_level_table_rebuild_persists_and_get(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, _ = await engine.execute(guild_ctx, session, "set level-table function")
    session, _ = await engine.execute(guild_ctx, session, "set function-type quadratic")
    session, _ = await engine.execute(guild_ctx, session, "set function-a 1")
    session, _ = await engine.execute(guild_ctx, session, "set function-b 0")
    session, _ = await engine.execute(guild_ctx, session, "set function-c 0")
    session, _ = await engine.execute(guild_ctx, session, "set max-level 3")
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    session, result = await engine.execute(guild_ctx, session, "diagnose level-table rebuild")
    assert "ok rebuilt rows=3" in result.output
    session, result = await engine.execute(guild_ctx, session, "get level-table")
    assert "level table (rebuilt):" in result.output
    assert "level=1 required_total_xp=1 delta_xp=1 segment=function:quadratic" in result.output
    assert "level=3 required_total_xp=9 delta_xp=5 segment=function:quadratic" in result.output


@pytest.mark.asyncio
async def test_level_table_deploy_does_not_rebuild(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter level-common")
    session, _ = await engine.execute(guild_ctx, session, "set max-level 5")
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    session, result = await engine.execute(guild_ctx, session, "get level-table")
    assert "(empty)" in result.output


@pytest.mark.asyncio
async def test_management_module_level_toggle(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)
    session, _ = await engine.execute(guild_ctx, session, "enter management-module")
    session, _ = await engine.execute(guild_ctx, session, "set level enable")
    session, _ = await engine.execute(guild_ctx, session, "deploy")
    assert await storage.is_management_module_enabled(guild_ctx.guild_id, "level") is True


@pytest.mark.asyncio
async def test_get_level_me_user_ranking(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_level_user(guild_ctx.guild_id, guild_ctx.actor_user_id, total_xp=321, level=7)
    await storage.upsert_level_user(guild_ctx.guild_id, 222, total_xp=200, level=5)
    await storage.replace_level_table(
        guild_ctx.guild_id,
        [
            {"level": 1, "required_total_xp": 100, "delta_xp": 100, "segment": "fixed"},
            {"level": 8, "required_total_xp": 400, "delta_xp": 100, "segment": "fixed"},
        ],
    )
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, result = await engine.execute(guild_ctx, session, "get level me")
    assert f"user={guild_ctx.actor_user_id}" in result.output
    assert "level=7" in result.output

    session, result = await engine.execute(guild_ctx, session, "get level user 222")
    assert "user=222" in result.output
    assert "total_xp=200" in result.output

    session, result = await engine.execute(guild_ctx, session, "get level ranking 5")
    assert "level ranking" in result.output
    assert "1. user=" in result.output


@pytest.mark.asyncio
async def test_cli_command_audit_logs_written(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter welcome")
    session, _ = await engine.execute(guild_ctx, session, "set join-roles 1")

    rows = await storage.fetch_logs("audit", scope_id=guild_ctx.guild_id, limit=20)
    actions = [row.action for row in rows]
    assert "command:enter" in actions
    assert "command:set" in actions


@pytest.mark.asyncio
async def test_cli_command_audit_enter_uses_updated_section_context(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter welcome")

    rows = await storage.fetch_logs("audit", scope_id=guild_ctx.guild_id, limit=20)
    enter_rows = [row for row in rows if row.action == "command:enter"]
    assert enter_rows
    assert enter_rows[0].section == "welcome"


@pytest.mark.asyncio
async def test_deploy_uses_section_schema_version(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    original_version = engine.sections["welcome"].schema_version
    engine.sections["welcome"].schema_version = 99
    try:
        session, _ = await engine.execute(guild_ctx, session, "enter welcome")
        session, _ = await engine.execute(guild_ctx, session, "set join-roles 1")
        session, _ = await engine.execute(guild_ctx, session, "deploy")
        row = await storage.load_config("guild", guild_ctx.guild_id, "welcome")
        assert row is not None
        assert row.data.get("schema_version") == 99
    finally:
        engine.sections["welcome"].schema_version = original_version


@pytest.mark.asyncio
async def test_set_category_question_in_mod_log_aliases_type(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter guild-log")
    session, _ = await engine.execute(guild_ctx, session, "enter mod-log")
    session, result = await engine.execute(guild_ctx, session, "set category ?")
    assert "ban" in result.output
    assert "timeout" in result.output


@pytest.mark.asyncio
async def test_fullwidth_question_completion_supported(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter guild-log")
    session, _ = await engine.execute(guild_ctx, session, "enter message-log")
    session, result = await engine.execute(guild_ctx, session, "set category ？")
    assert "delete" in result.output
    assert "edit" in result.output


@pytest.mark.asyncio
async def test_message_log_tracking_mode_set_and_completion(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter guild-log")
    session, _ = await engine.execute(guild_ctx, session, "enter message-log")
    session, result = await engine.execute(guild_ctx, session, "set tracking-message-mode ?")
    assert "normal" in result.output
    assert "extra" in result.output

    session, result = await engine.execute(guild_ctx, session, "set tracking-message-mode extra")
    assert result.output == "ok"


@pytest.mark.asyncio
async def test_get_guild_log_message_cache_status(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    guild_message_cache.put(
        guild_ctx.guild_id,
        CachedMessage(
            message_id=9999,
            channel_id=7777,
            author_id=123,
            author_name="tester",
            content="hello-cache",
        ),
        limit=1000,
    )
    session, result = await engine.execute(guild_ctx, session, "get guild-log message cache status")
    assert "guild-log message cache status guild=" in result.output
    assert "mode=normal" in result.output
    assert "entries=" in result.output
    assert "estimated_bytes=" in result.output
    assert "hit_rate=" in result.output


@pytest.mark.asyncio
async def test_engine_garbage_inputs_do_not_return_fatal(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    lines = [
        "",
        "   ",
        'set welcome-message "unterminated',
        "unknown-command",
        "enter //////",
        "select not-an-id",
        "set",
        "unset",
        "switch",
        "execute",
        "execute cli to-file start maybe",
        "get level user not-number",
        "diagnose ???",
        "show unexpected-arg",
        "move x before y",
        "insert nope",
        "？？？",
        "# comment only",
    ]
    for line in lines:
        session, result = await engine.execute(guild_ctx, session, line)
        assert not result.output.lower().startswith("fatal error:")


@pytest.mark.asyncio
async def test_chat_group_global_root_section_set_show(monkeypatch, tmp_path, admin_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)

    session, _ = await engine.initialize_session(admin_ctx)
    session, _ = await engine.execute(admin_ctx, session, "switch root")
    session, result = await engine.execute(admin_ctx, session, "enter chat-group-global")
    assert result.output == "ok"
    session, result = await engine.execute(admin_ctx, session, "set attachment-channel-id 12345")
    assert result.output == "ok"
    session, result = await engine.execute(admin_ctx, session, "show now-config")
    assert "enter chat-group-global" in result.output
    assert "set attachment-channel-id 12345" in result.output


@pytest.mark.asyncio
async def test_chat_group_global_forbidden_in_guild_scope(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)
    session, result = await engine.execute(guild_ctx, session, "enter chat-group-global")
    assert "forbidden" in result.output


@pytest.mark.asyncio
async def test_chat_group_section_select_and_nested_set(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter chat-group")
    session, result = await engine.execute(guild_ctx, session, "select 1")
    assert result.output == "selected 1"
    session, _ = await engine.execute(guild_ctx, session, 'set name "alpha"')
    session, _ = await engine.execute(guild_ctx, session, "set mode private")
    session, _ = await engine.execute(guild_ctx, session, "enter connection")
    session, _ = await engine.execute(guild_ctx, session, "set channel 12345")
    session, _ = await engine.execute(guild_ctx, session, "top")
    session, _ = await engine.execute(guild_ctx, session, "enter chat-group")
    session, result = await engine.execute(guild_ctx, session, "show now-config")
    assert "enter chat-group" in result.output
    assert "set name \"alpha\"" in result.output
    assert "enter connection" in result.output
    assert "set channel 12345" in result.output


@pytest.mark.asyncio
async def test_chat_group_group_id_is_read_only(monkeypatch, tmp_path, guild_ctx: EngineContext):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    engine = CliEngine(storage)
    session, _ = await engine.initialize_session(guild_ctx)

    session, _ = await engine.execute(guild_ctx, session, "enter chat-group")
    session, _ = await engine.execute(guild_ctx, session, "select 1")
    session, result = await engine.execute(guild_ctx, session, 'set group-id "cg-manual"')
    assert "reason=readonly" in result.output

    session, result = await engine.execute(guild_ctx, session, "set ?")
    assert "group-id" in result.output
