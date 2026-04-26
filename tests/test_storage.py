from __future__ import annotations

import asyncio

import pytest

from utils.storage import Storage


@pytest.mark.asyncio
async def test_is_management_module_enabled_default_true_when_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    assert await storage.is_management_module_enabled(123, "welcome") is True


@pytest.mark.asyncio
async def test_is_management_module_enabled_reads_running_payload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        123,
        "management-module",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {"welcome": False},
                "startup_payload": {"welcome": True},
            },
        },
    )
    assert await storage.is_management_module_enabled(123, "welcome") is False


@pytest.mark.asyncio
async def test_is_management_module_enabled_legacy_payload_compat(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        123,
        "management-module",
        {
            "schema_version": 1,
            "payload": {"welcome": False},
        },
    )
    assert await storage.is_management_module_enabled(123, "welcome") is False


@pytest.mark.asyncio
async def test_replace_level_table_overwrites_all_rows(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.replace_level_table(
        123,
        [
            {"level": 1, "required_total_xp": 100, "delta_xp": 100, "segment": "fixed"},
            {"level": 2, "required_total_xp": 200, "delta_xp": 100, "segment": "fixed"},
        ],
    )
    await storage.replace_level_table(
        123,
        [
            {"level": 1, "required_total_xp": 10, "delta_xp": 10, "segment": "static-table"},
        ],
    )
    rows = await storage.fetch_level_table(123)
    assert len(rows) == 1
    assert rows[0]["required_total_xp"] == 10
    assert rows[0]["segment"] == "static-table"


@pytest.mark.asyncio
async def test_fetch_level_table_ordering_and_fields(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.replace_level_table(
        77,
        [
            {"level": 3, "required_total_xp": 330, "delta_xp": 130, "segment": "function:exponential"},
            {"level": 1, "required_total_xp": 100, "delta_xp": 100, "segment": "function:exponential"},
            {"level": 2, "required_total_xp": 200, "delta_xp": 100, "segment": "function:exponential"},
        ],
    )
    rows = await storage.fetch_level_table(77)
    assert [row["level"] for row in rows] == [1, 2, 3]
    assert all("rebuilt_at" in row for row in rows)


@pytest.mark.asyncio
async def test_upsert_config_increments_version(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    version1 = await storage.upsert_config("guild", 1, "welcome", {"payload": {"v": 1}})
    version2 = await storage.upsert_config("guild", 1, "welcome", {"payload": {"v": 2}})
    row = await storage.load_config("guild", 1, "welcome")

    assert version1 == 1
    assert version2 == 2
    assert row is not None
    assert row.version == 2
    assert row.data["payload"]["v"] == 2


@pytest.mark.asyncio
async def test_fetch_logs_limit_is_clamped(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    for index in range(3):
        await storage.insert_audit_log(
            actor_user_id=1,
            scope_type="guild",
            scope_id=10,
            section="welcome",
            action=f"a{index}",
            before_json=None,
            after_json=None,
            result="ok",
        )

    rows = await storage.fetch_logs("audit", scope_id=10, limit=0)
    assert len(rows) == 1
    assert rows[0].action == "a2"


@pytest.mark.asyncio
async def test_trim_logs_keeps_latest_rows(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    for index in range(10):
        await storage.insert_audit_log(
            actor_user_id=1,
            scope_type="guild",
            scope_id=88,
            section="welcome",
            action=f"a{index}",
            before_json=None,
            after_json=None,
            result="ok",
        )
        await storage.insert_system_log(
            actor_user_id=1,
            scope_id=88,
            feature="welcome",
            severity="info",
            message=f"m{index}",
            detail_json=None,
        )

    await storage.trim_logs(scope_id=88, audit_max=3, system_max=4)
    audit_rows = await storage.fetch_logs("audit", scope_id=88, limit=100)
    system_rows = await storage.fetch_logs("system", scope_id=88, limit=100)
    assert [row.action for row in audit_rows] == ["a9", "a8", "a7"]
    assert [row.result for row in system_rows] == ["m9", "m8", "m7", "m6"]


@pytest.mark.asyncio
async def test_insert_system_log_safe_returns_false_on_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def _raise(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(storage, "insert_system_log", _raise)
    ok = await storage.insert_system_log_safe(
        actor_user_id=1,
        scope_id=88,
        feature="x",
        severity="warn",
        message="y",
        detail_json={},
    )
    assert ok is False


@pytest.mark.asyncio
async def test_insert_audit_log_safe_returns_false_on_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def _raise(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(storage, "insert_audit_log", _raise)
    ok = await storage.insert_audit_log_safe(
        actor_user_id=1,
        scope_type="guild",
        scope_id=88,
        section="x",
        action="y",
        before_json=None,
        after_json=None,
        result="ok",
    )
    assert ok is False


@pytest.mark.asyncio
async def test_insert_crash_log_safe_returns_false_on_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def _raise(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(storage, "insert_crash_log", _raise)
    ok, error_id = await storage.insert_crash_log_safe(
        scope_type="guild",
        scope_id=88,
        actor_user_id=1,
        section="x",
        command="y",
        message="boom",
        traceback_text="tb",
        context_json={},
        forward_mode="off",
        forward_status="drop",
        error_id="CR-TESTID",
    )
    assert ok is False
    assert error_id == "CR-TESTID"


@pytest.mark.asyncio
async def test_fetch_logs_safe_returns_empty_on_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def _raise(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(storage, "fetch_logs", _raise)
    rows = await storage.fetch_logs_safe("system", scope_id=88, limit=10)
    assert rows == []


@pytest.mark.asyncio
async def test_fetch_crash_safe_returns_empty_or_none_on_failure(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def _raise_list(*args, **kwargs):
        raise RuntimeError("db unavailable")

    async def _raise_one(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(storage, "fetch_crash_logs", _raise_list)
    monkeypatch.setattr(storage, "fetch_crash_log_by_error_id", _raise_one)
    rows = await storage.fetch_crash_logs_safe("guild", scope_id=88, limit=10)
    row = await storage.fetch_crash_log_by_error_id_safe("guild", scope_id=88, error_id="CR-x")
    assert rows == []
    assert row is None


@pytest.mark.asyncio
async def test_insert_crash_log_and_fetch_by_error_id(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    error_id = await storage.insert_crash_log(
        scope_type="guild",
        scope_id=99,
        actor_user_id=3,
        section="global",
        command="where",
        message="boom",
        traceback_text="trace",
        context_json={"a": 1},
        forward_mode="off",
        forward_status="drop",
    )
    row = await storage.fetch_crash_log_by_error_id("guild", 99, error_id)
    assert row is not None
    assert row.error_id == error_id
    assert row.context_json["a"] == 1


@pytest.mark.asyncio
async def test_fetch_crash_logs_limit_clamped(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    for index in range(5):
        await storage.insert_crash_log(
            scope_type="guild",
            scope_id=5,
            actor_user_id=1,
            section="global",
            command="x",
            message=f"e{index}",
            traceback_text="tb",
            context_json={},
            forward_mode="off",
            forward_status="drop",
        )
    rows = await storage.fetch_crash_logs("guild", 5, 0)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_trim_crash_logs_keeps_one_with_zero_max(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    for index in range(3):
        await storage.insert_crash_log(
            scope_type="guild",
            scope_id=6,
            actor_user_id=1,
            section="global",
            command="x",
            message=f"e{index}",
            traceback_text="tb",
            context_json={},
            forward_mode="off",
            forward_status="drop",
        )
    await storage.trim_crash_logs("guild", 6, 0)
    rows = await storage.fetch_crash_logs("guild", 6, 100)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_insert_root_crash_copy_creates_root_row(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    copied_id = await storage.insert_root_crash_copy(
        source_error_id="CR-source",
        scope_id=123,
        context_json={"section": "welcome", "command": "set", "traceback": "tb"},
    )
    row = await storage.fetch_crash_log_by_error_id("root", 123, copied_id)
    assert row is not None
    assert row.scope_type == "root"
    assert row.message == "root-received from CR-source"


@pytest.mark.asyncio
async def test_resolve_receive_config_defaults_when_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    config = await storage.resolve_receive_config()
    assert config.receive_mode == "off"
    assert config.crashlog_report_channel is None
    assert config.crashlog_max_buffer == 500


@pytest.mark.asyncio
async def test_resolve_tick_config_defaults_when_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    config = await storage.resolve_tick_config(555)
    assert config.max_tick_limit == 3000
    assert config.overlimit_mode == "alert-only"


@pytest.mark.asyncio
async def test_resolve_tick_config_uses_guild_control_plane_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        556,
        "control-plane",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {"tick": {"max_tick_limit": 4500, "overlimit_mode": "drop-new-work"}},
                "startup_payload": {"tick": {"max_tick_limit": 4500, "overlimit_mode": "drop-new-work"}},
            },
        },
    )
    config = await storage.resolve_tick_config(556)
    assert config.max_tick_limit == 4500
    assert config.overlimit_mode == "drop-new-work"


@pytest.mark.asyncio
async def test_resolve_tick_config_root_enforce_overrides_guild(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        557,
        "control-plane",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {"tick": {"max_tick_limit": 4500, "overlimit_mode": "alert-only"}},
                "startup_payload": {"tick": {"max_tick_limit": 4500, "overlimit_mode": "alert-only"}},
            },
        },
    )
    await storage.upsert_config(
        "root",
        0,
        "root-enforce",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "sections": {"control-plane/tick": {"max-tick-limit": 9000, "overlimit-mode": "drop-new-work"}}
                }
            },
        },
    )
    config = await storage.resolve_tick_config(557)
    assert config.max_tick_limit == 9000
    assert config.overlimit_mode == "drop-new-work"


@pytest.mark.asyncio
async def test_resolve_tick_config_root_override_has_highest_priority(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "root",
        0,
        "root-enforce",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "sections": {"control-plane/tick": {"max-tick-limit": 9000, "overlimit-mode": "drop-new-work"}}
                }
            },
        },
    )
    await storage.upsert_config(
        "root",
        0,
        "root-enforce-override",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "guilds": {
                        "558": {
                            "sections": {
                                "control-plane/tick": {"max-tick-limit": 4200, "overlimit-mode": "alert-only"}
                            }
                        }
                    }
                }
            },
        },
    )
    config = await storage.resolve_tick_config(558)
    assert config.max_tick_limit == 4200
    assert config.overlimit_mode == "alert-only"


@pytest.mark.asyncio
async def test_resolve_tick_config_clamps_limits(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        559,
        "control-plane",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {"tick": {"max_tick_limit": 1}},
                "startup_payload": {"tick": {"max_tick_limit": 1}},
            },
        },
    )
    low = await storage.resolve_tick_config(559)
    assert low.max_tick_limit == 100

    await storage.upsert_config(
        "guild",
        560,
        "control-plane",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {"tick": {"max_tick_limit": 999999999}},
                "startup_payload": {"tick": {"max_tick_limit": 999999999}},
            },
        },
    )
    high = await storage.resolve_tick_config(560)
    assert high.max_tick_limit == 1000000


@pytest.mark.asyncio
async def test_level_user_upsert_and_fetch(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    assert await storage.get_level_user(1, 2) is None
    await storage.upsert_level_user(1, 2, total_xp=123, level=4)
    row = await storage.get_level_user(1, 2)
    assert row is not None
    assert row.total_xp == 123
    assert row.level == 4


@pytest.mark.asyncio
async def test_level_runtime_upsert_and_clear(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    row = await storage.get_level_runtime(1, 2)
    assert row.voice_joined_at is None

    await storage.upsert_level_runtime(1, 2, voice_joined_at="2026-01-01T00:00:00+00:00")
    row = await storage.get_level_runtime(1, 2)
    assert row.voice_joined_at == "2026-01-01T00:00:00+00:00"

    await storage.upsert_level_runtime(1, 2, clear_voice_joined_at=True)
    row = await storage.get_level_runtime(1, 2)
    assert row.voice_joined_at is None


@pytest.mark.asyncio
async def test_fetch_level_ranking_sorted(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    await storage.upsert_level_user(5, 11, total_xp=50, level=1)
    await storage.upsert_level_user(5, 22, total_xp=200, level=3)
    await storage.upsert_level_user(5, 33, total_xp=120, level=2)
    rows = await storage.fetch_level_ranking(5, 10)
    assert [item.user_id for item in rows] == [22, 33, 11]


@pytest.mark.asyncio
async def test_resolve_receive_config_reads_running_payload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "root",
        0,
        "tenant-connection",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "log": {
                        "receive_mode": "both",
                        "crashlog_report_channel": 777,
                        "crashlog_max_buffer": 900,
                    }
                },
                "startup_payload": {"log": {"receive_mode": "off"}},
            },
        },
    )
    config = await storage.resolve_receive_config()
    assert config.receive_mode == "both"
    assert config.crashlog_report_channel == 777
    assert config.crashlog_max_buffer == 900


@pytest.mark.asyncio
async def test_resolve_receive_config_invalid_types_fallback(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "root",
        0,
        "tenant-connection",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "log": {
                        "receive_mode": "discord",
                        "crashlog_report_channel": "not-int",
                        "crashlog_max_buffer": "bad",
                    }
                }
            },
        },
    )
    config = await storage.resolve_receive_config()
    assert config.receive_mode == "discord"
    assert config.crashlog_report_channel is None
    assert config.crashlog_max_buffer == 500


@pytest.mark.asyncio
async def test_resolve_tick_config_defaults_when_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    config = await storage.resolve_tick_config(10)
    assert config.max_tick_limit == 3000
    assert config.overlimit_mode == "alert-only"


@pytest.mark.asyncio
async def test_resolve_tick_config_root_and_guild_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "root",
        0,
        "root-enforce",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "sections": {
                        "control-plane/tick": {
                            "max-tick-limit": 7000,
                            "overlimit-mode": "alert-only",
                        }
                    }
                },
                "startup_payload": {},
            },
        },
    )
    await storage.upsert_config(
        "guild",
        55,
        "control-plane",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {"tick": {"max_tick_limit": 9000, "overlimit_mode": "drop-new-work"}},
                "startup_payload": {},
            },
        },
    )
    config = await storage.resolve_tick_config(55)
    assert config.max_tick_limit == 7000
    assert config.overlimit_mode == "alert-only"


@pytest.mark.asyncio
async def test_resolve_tick_config_root_enforce_has_highest_priority(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "guild",
        56,
        "control-plane",
        {
            "schema_version": 1,
            "payload": {"running_payload": {"tick": {"max_tick_limit": 9000, "overlimit_mode": "drop-new-work"}}},
        },
    )
    await storage.upsert_config(
        "root",
        0,
        "root-enforce",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "sections": {
                        "control-plane/tick": {
                            "max-tick-limit": 3000,
                            "overlimit-mode": "alert-only",
                        }
                    }
                }
            },
        },
    )
    config = await storage.resolve_tick_config(56)
    assert config.max_tick_limit == 3000
    assert config.overlimit_mode == "alert-only"


@pytest.mark.asyncio
async def test_resolve_tick_config_root_override_per_guild_has_highest_priority(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "root",
        0,
        "root-enforce",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "sections": {
                        "control-plane/tick": {
                            "max-tick-limit": 3000,
                            "overlimit-mode": "alert-only",
                        }
                    }
                }
            },
        },
    )
    await storage.upsert_config(
        "root",
        0,
        "root-enforce-override",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "guilds": {
                        "56": {
                            "sections": {
                                "control-plane/tick": {
                                    "max-tick-limit": 4200,
                                    "overlimit-mode": "drop-new-work",
                                }
                            }
                        }
                    }
                }
            },
        },
    )
    config = await storage.resolve_tick_config(56)
    assert config.max_tick_limit == 4200
    assert config.overlimit_mode == "drop-new-work"


@pytest.mark.asyncio
async def test_resolve_tick_config_accepts_legacy_control_plane_dotted_tick_keys(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    await storage.upsert_config(
        "root",
        0,
        "root-enforce",
        {
            "schema_version": 1,
            "payload": {
                "running_payload": {
                    "sections": {
                        "control-plane": {
                            "tick.max-tick-limit": 9100,
                            "tick.overlimit-mode": "drop-new-work",
                        }
                    }
                }
            },
        },
    )
    config = await storage.resolve_tick_config(999)
    assert config.max_tick_limit == 9100
    assert config.overlimit_mode == "drop-new-work"


@pytest.mark.asyncio
async def test_is_management_module_enabled_unknown_module_returns_true(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    assert await storage.is_management_module_enabled(123, "unknown-module") is True


@pytest.mark.asyncio
async def test_healthcheck_sqlite_ok(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    health = await storage.healthcheck()
    assert health.ok is True
    assert health.backend == "sqlite"


@pytest.mark.asyncio
async def test_sticky_runtime_upsert_get_delete(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    await storage.upsert_sticky_runtime(123, 456, 789, "sig-1")
    row = await storage.get_sticky_runtime(123, 456)
    assert row is not None
    assert row.message_id == 789
    assert row.signature == "sig-1"

    await storage.upsert_sticky_runtime(123, 456, 790, "sig-2")
    row = await storage.get_sticky_runtime(123, 456)
    assert row is not None
    assert row.message_id == 790
    assert row.signature == "sig-2"

    await storage.delete_sticky_runtime(123, 456)
    assert await storage.get_sticky_runtime(123, 456) is None


@pytest.mark.asyncio
async def test_chat_group_core_storage_flow(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    group_id = await storage.create_chat_group(
        name="test-group",
        mode="public",
        leader_guild_id=123,
        channel_id=9991,
    )
    group = await storage.get_chat_group(group_id)
    assert group is not None
    assert group.mode == "public"
    assert group.leader_guild_id == 123

    await storage.upsert_chat_group_membership(group_id=group_id, guild_id=456, status="active", role="normal")
    await storage.upsert_chat_group_connection(
        group_id=group_id,
        guild_id=456,
        channel_id=9992,
        webhook_ref=None,
    )
    memberships = await storage.list_chat_group_memberships(group_id)
    assert {row.guild_id for row in memberships} == {123, 456}

    apply_id = await storage.create_chat_group_application(group_id, 789, 9993)
    pending = await storage.list_chat_group_applications(group_id, status="pending")
    assert any(row.apply_id == apply_id for row in pending)
    decided = await storage.decide_chat_group_application(apply_id, "approved", decided_by=1234)
    assert decided is not None
    assert decided.status == "approved"

    key_id, key_plain = await storage.create_chat_group_auth_key(group_id, guild_id=456)
    assert await storage.resolve_chat_group_auth_key(group_id, key_plain, 456) is True
    assert await storage.resolve_chat_group_auth_key(group_id, key_plain, 457) is False
    assert await storage.revoke_chat_group_auth_key(group_id, key_id) is True
    assert await storage.resolve_chat_group_auth_key(group_id, key_plain, 456) is False

    message_id = await storage.insert_chat_group_message(
        group_id=group_id,
        source_guild_id=123,
        source_channel_id=9991,
        source_message_id=1111,
        author_user_id=5001,
        author_name="alice",
        content="hello",
        attachment_urls=["https://cdn.example/a.png"],
    )
    await storage.insert_chat_group_delivery(
        message_id=message_id,
        group_id=group_id,
        target_guild_id=456,
        target_channel_id=9992,
        target_message_id=2222,
        status="ok",
        error=None,
    )
    snapshot = await storage.get_chat_group_message(message_id)
    assert snapshot is not None
    assert snapshot.content == "hello"
    deliveries = await storage.list_chat_group_deliveries(message_id)
    assert len(deliveries) == 1
    assert deliveries[0]["target_message_id"] == 2222


@pytest.mark.asyncio
async def test_chat_group_ban_scopes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    group_id = await storage.create_chat_group(
        name="ban-group",
        mode="public",
        leader_guild_id=111,
        channel_id=9991,
    )
    user_id = 777
    assert await storage.is_chat_group_user_banned(group_id, 111, user_id) is False

    await storage.set_chat_group_ban(group_id=group_id, guild_id=None, user_id=user_id, mode="ban", global_scope=False)
    assert await storage.is_chat_group_user_banned(group_id, 111, user_id) is True
    await storage.set_chat_group_ban(group_id=group_id, guild_id=None, user_id=user_id, mode="unban", global_scope=False)
    assert await storage.is_chat_group_user_banned(group_id, 111, user_id) is False

    await storage.set_chat_group_ban(group_id=None, guild_id=111, user_id=user_id, mode="ban", global_scope=False)
    assert await storage.is_chat_group_user_banned(group_id, 111, user_id) is True
    await storage.set_chat_group_ban(group_id=None, guild_id=111, user_id=user_id, mode="unban", global_scope=False)
    assert await storage.is_chat_group_user_banned(group_id, 111, user_id) is False

    await storage.set_chat_group_ban(group_id=None, guild_id=None, user_id=user_id, mode="ban", global_scope=True)
    assert await storage.is_chat_group_user_banned(group_id, 111, user_id) is True


@pytest.mark.asyncio
async def test_chat_group_cli_index_stable_and_monotonic(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    mapping1 = await storage.resolve_chat_group_cli_index(9101, ["cg-b", "cg-a"])
    assert mapping1["cg-a"] == 1
    assert mapping1["cg-b"] == 2

    mapping2 = await storage.resolve_chat_group_cli_index(9101, ["cg-b", "cg-c"])
    assert mapping2["cg-b"] == 2
    assert mapping2["cg-c"] == 3

    mapping3 = await storage.resolve_chat_group_cli_index(9101, ["cg-c"])
    assert mapping3["cg-c"] == 3


@pytest.mark.asyncio
async def test_chat_group_application_decide_pending_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    group_id = await storage.create_chat_group(
        name="pending-only",
        mode="private",
        leader_guild_id=9201,
        channel_id=9920,
    )
    apply_id = await storage.create_chat_group_application(group_id, 9202, 9921)
    first = await storage.decide_chat_group_application(apply_id, "approved", decided_by=1111)
    assert first is not None
    assert first.status == "approved"

    second = await storage.decide_chat_group_application(apply_id, "denied", decided_by=2222)
    assert second is None


@pytest.mark.asyncio
async def test_chat_group_cli_index_parallel_allocation_no_duplicates(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def resolve_batch(batch: list[str]) -> dict[str, int]:
        return await storage.resolve_chat_group_cli_index(9301, batch)

    results = await asyncio.gather(
        resolve_batch(["cg-1", "cg-2", "cg-3"]),
        resolve_batch(["cg-2", "cg-4"]),
        resolve_batch(["cg-5"]),
    )
    _ = results

    mapping = await storage.resolve_chat_group_cli_index(9301, ["cg-1", "cg-2", "cg-3", "cg-4", "cg-5"])
    assert set(mapping.keys()) == {"cg-1", "cg-2", "cg-3", "cg-4", "cg-5"}
    assert len(set(mapping.values())) == 5


@pytest.mark.asyncio
async def test_chat_group_rate_limit_state_defaults_and_clamps(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    state0 = await storage.get_chat_group_rate_limit_state("cg-rate-1")
    assert state0.group_id == "cg-rate-1"
    assert state0.queued_count == 0
    assert state0.inflight_count == 0

    state1 = await storage.increment_chat_group_queue("cg-rate-1", queued_delta=3, inflight_delta=2)
    assert state1.queued_count == 3
    assert state1.inflight_count == 2

    state2 = await storage.increment_chat_group_queue("cg-rate-1", queued_delta=-10, inflight_delta=-10)
    assert state2.queued_count == 0
    assert state2.inflight_count == 0


@pytest.mark.asyncio
async def test_chat_group_rate_limit_state_reset_supports_all_and_group(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    await storage.increment_chat_group_queue("cg-rate-1", queued_delta=2, inflight_delta=1)
    await storage.increment_chat_group_queue("cg-rate-2", queued_delta=4, inflight_delta=3)

    changed_one = await storage.reset_chat_group_rate_limit_states("cg-rate-1")
    assert changed_one >= 1
    state_one = await storage.get_chat_group_rate_limit_state("cg-rate-1")
    state_two = await storage.get_chat_group_rate_limit_state("cg-rate-2")
    assert state_one.queued_count == 0
    assert state_one.inflight_count == 0
    assert state_two.queued_count == 4
    assert state_two.inflight_count == 3

    changed_all = await storage.reset_chat_group_rate_limit_states()
    assert changed_all >= 1
    state_two_after = await storage.get_chat_group_rate_limit_state("cg-rate-2")
    assert state_two_after.queued_count == 0
    assert state_two_after.inflight_count == 0
