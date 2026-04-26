from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils.level.service import LevelEventContext, LevelService
from utils.storage import Storage


async def _put_running(storage: Storage, guild_id: int, section: str, payload: dict):
    await storage.upsert_config(
        "guild",
        guild_id,
        section,
        {"schema_version": 1, "payload": {"running_payload": payload, "startup_payload": payload}},
    )


@pytest.mark.asyncio
async def test_level_service_message_gain(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 100
    user_id = 200

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": False, "multiplier": 1.0, "gain_time": 10, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-message", {"gain_mode": "static", "gain_xp": 10, "gain_time": 10})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": []})

    service = LevelService(storage)
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    result = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=now,
            message_length=20,
        )
    )
    assert result.applied_xp == 10
    assert result.new_total_xp == 10


@pytest.mark.asyncio
async def test_level_service_cooldown(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 101
    user_id = 201

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": False, "multiplier": 1.0, "gain_time": 60, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-message", {"gain_mode": "static", "gain_xp": 5, "gain_time": 60})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": []})

    service = LevelService(storage)
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    first = await service.apply_event(
        LevelEventContext(guild_id=guild_id, user_id=user_id, event_type="message", channel_id=1, role_ids=[], occurred_at=now, message_length=5)
    )
    second = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=now + timedelta(seconds=10),
            message_length=5,
        )
    )
    assert first.applied_xp == 5
    assert second.applied_xp == 0
    assert second.reason == "cooldown"


@pytest.mark.asyncio
async def test_level_service_policy_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 102
    user_id = 202

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": True, "multiplier": 1.0, "gain_time": 10, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-message", {"gain_mode": "static", "gain_xp": 2, "gain_time": 10})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": [9]})
    await _put_running(
        storage,
        guild_id,
        "level-gain-policy",
        {
            "policies": [
                {
                    "id": 1,
                    "name": "override",
                    "action": "override",
                    "channels": "any",
                    "roles": "any",
                    "method": "message",
                    "gain_mode": "static",
                    "gain_xp": 30,
                    "gain_range_min": 1,
                    "gain_range_max": 1,
                    "gain_time": 1,
                    "time_start": None,
                    "time_end": None,
                },
                {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            ]
        },
    )

    service = LevelService(storage)
    result = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=9,
            role_ids=[],
            occurred_at=datetime.now(timezone.utc),
            message_length=30,
        )
    )
    assert result.applied_xp == 30


@pytest.mark.asyncio
async def test_level_service_voice_leave_grants(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 103
    user_id = 203

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": False, "multiplier": 1.0, "gain_time": 10, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-voice", {"gain_mode": "static", "gain_xp": 2, "gain_time": 10})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": []})

    service = LevelService(storage)
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    await service.mark_voice_join(guild_id, user_id, now - timedelta(seconds=35))
    result = await service.apply_voice_leave(guild_id, user_id, channel_id=1, role_ids=[], occurred_at=now)
    assert result.applied_xp == 6


@pytest.mark.asyncio
async def test_level_service_respects_module_disable(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 104
    user_id = 204

    await _put_running(storage, guild_id, "management-module", {"level": False, "welcome": True})
    service = LevelService(storage)
    result = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=datetime.now(timezone.utc),
            message_length=10,
        )
    )
    assert result.applied_xp == 0
    assert result.reason == "module-disabled"


@pytest.mark.asyncio
async def test_level_service_respects_tick_overlimit_drop_new_work(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 105
    user_id = 205

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": False, "multiplier": 1.0, "gain_time": 1, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-message", {"gain_mode": "static", "gain_xp": 1, "gain_time": 1})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": []})
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
                            "max-tick-limit": 100,
                            "overlimit-mode": "drop-new-work",
                        }
                    }
                }
            },
        },
    )

    service = LevelService(storage)
    await service.tick_meter.consume(guild_id, "test.prefill", amount=100, stoppable=False)
    now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    result = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=now,
            message_length=10,
        )
    )
    assert result.reason == "tick-over-limit"


@pytest.mark.asyncio
async def test_level_service_invalid_policy_time_logs_warn_once_per_minute(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 106
    user_id = 206

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": True, "multiplier": 1.0, "gain_time": 1, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-message", {"gain_mode": "static", "gain_xp": 1, "gain_time": 1})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": []})
    await _put_running(
        storage,
        guild_id,
        "level-gain-policy",
        {
            "policies": [
                {
                    "id": 1,
                    "name": "broken-time",
                    "action": "override",
                    "channels": "any",
                    "roles": "any",
                    "method": "message",
                    "gain_mode": "static",
                    "gain_xp": 10,
                    "gain_range_min": 1,
                    "gain_range_max": 1,
                    "gain_time": 1,
                    "time_start": "99:99",
                    "time_end": "ab:cd",
                },
                {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            ]
        },
    )

    service = LevelService(storage)
    now = datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc)

    first = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=now,
            message_length=20,
        )
    )
    second = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=now + timedelta(seconds=20),
            message_length=20,
        )
    )
    assert first.reason == "applied"
    assert second.reason == "applied"

    logs = await storage.fetch_logs("system", scope_id=guild_id, limit=50)
    invalid_logs = [row for row in logs if row.section == "level-policy" and row.action == "warn" and row.result == "invalid-policy-time-window"]
    assert len(invalid_logs) == 1


@pytest.mark.asyncio
async def test_level_service_invalid_policy_time_log_failure_keeps_processing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 1061
    user_id = 2061

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": True, "multiplier": 1.0, "gain_time": 1, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-message", {"gain_mode": "static", "gain_xp": 1, "gain_time": 1})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": []})
    await _put_running(
        storage,
        guild_id,
        "level-gain-policy",
        {
            "policies": [
                {
                    "id": 1,
                    "name": "broken-time",
                    "action": "override",
                    "channels": "any",
                    "roles": "any",
                    "method": "message",
                    "gain_mode": "static",
                    "gain_xp": 10,
                    "gain_range_min": 1,
                    "gain_range_max": 1,
                    "gain_time": 1,
                    "time_start": "99:99",
                    "time_end": "ab:cd",
                },
                {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            ]
        },
    )

    async def _raise(*args, **kwargs):
        raise RuntimeError("system log unavailable")

    monkeypatch.setattr(storage, "insert_system_log", _raise)
    service = LevelService(storage)
    result = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=datetime(2026, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
            message_length=20,
        )
    )
    assert result.reason == "applied"
    status = await service.tick_meter.get_status(guild_id)
    sources = {row["source"]: row["used"] for row in status["sources"]}
    assert "log.system.write" not in sources


@pytest.mark.asyncio
async def test_level_service_policy_time_window_blocks_outside(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 107
    user_id = 207

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": True, "multiplier": 1.0, "gain_time": 1, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-message", {"gain_mode": "static", "gain_xp": 1, "gain_time": 1})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": []})
    await _put_running(
        storage,
        guild_id,
        "level-gain-policy",
        {
            "policies": [
                {
                    "id": 1,
                    "name": "daytime-only",
                    "action": "override",
                    "channels": "any",
                    "roles": "any",
                    "method": "message",
                    "gain_mode": "static",
                    "gain_xp": 10,
                    "gain_range_min": 1,
                    "gain_range_max": 1,
                    "gain_time": 1,
                    "time_start": "09:00",
                    "time_end": "10:00",
                },
                {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            ]
        },
    )

    service = LevelService(storage)
    out_of_window = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc),
            message_length=20,
        )
    )
    assert out_of_window.reason == "applied"
    assert out_of_window.applied_xp == 1


@pytest.mark.asyncio
async def test_level_service_policy_time_window_wraps_midnight(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    guild_id = 108
    user_id = 208

    await _put_running(storage, guild_id, "management-module", {"level": True, "welcome": True})
    await _put_running(storage, guild_id, "level-common", {"gain_policy": True, "multiplier": 1.0, "gain_time": 1, "fixed_step": 100})
    await _put_running(storage, guild_id, "level-method-message", {"gain_mode": "static", "gain_xp": 1, "gain_time": 1})
    await _put_running(storage, guild_id, "level-shared", {"mode": "blacklist", "channels": []})
    await _put_running(
        storage,
        guild_id,
        "level-gain-policy",
        {
            "policies": [
                {
                    "id": 1,
                    "name": "night",
                    "action": "override",
                    "channels": "any",
                    "roles": "any",
                    "method": "message",
                    "gain_mode": "static",
                    "gain_xp": 7,
                    "gain_range_min": 1,
                    "gain_range_max": 1,
                    "gain_time": 1,
                    "time_start": "23:00",
                    "time_end": "02:00",
                },
                {"id": 0, "name": "", "action": "gain", "channels": "any", "roles": "any", "method": "any"},
            ]
        },
    )

    service = LevelService(storage)
    in_window = await service.apply_event(
        LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="message",
            channel_id=1,
            role_ids=[],
            occurred_at=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
            message_length=20,
        )
    )
    assert in_window.applied_xp == 7
