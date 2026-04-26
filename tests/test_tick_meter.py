from __future__ import annotations

from datetime import datetime

import pytest

import utils.tick.meter as meter_module
from utils.storage import Storage
from utils.tick import TickMeter


@pytest.mark.asyncio
async def test_tick_meter_status_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    meter = TickMeter(storage)

    status = await meter.get_status(100)
    assert status["used"] == 0
    assert status["limit"] == 3000
    assert status["mode"] == "alert-only"
    assert status["categories"] == []
    assert status["sources"] == []


@pytest.mark.asyncio
async def test_tick_meter_source_and_category_aggregation(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    meter = TickMeter(storage)

    await meter.consume(101, "command.get", amount=3, stoppable=False)
    await meter.consume(101, "level.xp.calculate", amount=2, stoppable=False)
    await meter.consume(101, "log.system.write", amount=1, stoppable=False)

    status = await meter.get_status(101)
    assert status["used"] == 6
    assert status["categories"][0]["category"] in {"command", "level"}
    sources = {row["source"]: row["used"] for row in status["sources"]}
    assert sources["command.get"] == 3
    assert sources["level.xp.calculate"] == 2
    assert sources["log.system.write"] == 1


@pytest.mark.asyncio
async def test_tick_meter_start_work_drop_new_work(monkeypatch, tmp_path):
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
                    "sections": {"control-plane/tick": {"max-tick-limit": 100, "overlimit-mode": "drop-new-work"}}
                }
            },
        },
    )

    meter = TickMeter(storage)
    await meter.consume(102, "prefill", amount=100, stoppable=False)
    can_start = await meter.start_work(102, "level.event.message.entry", stoppable=True)
    assert can_start is False
    status = await meter.get_status(102)
    assert status["used"] >= 100


@pytest.mark.asyncio
async def test_tick_meter_emits_warn_logs_once(monkeypatch, tmp_path):
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
                    "sections": {"control-plane/tick": {"max-tick-limit": 100, "overlimit-mode": "alert-only"}}
                }
            },
        },
    )

    meter = TickMeter(storage)
    await meter.consume(103, "work", amount=90, stoppable=False)
    await meter.consume(103, "work", amount=20, stoppable=False)
    await meter.consume(103, "work", amount=5, stoppable=False)

    logs = await storage.fetch_logs("system", scope_id=103, limit=20)
    messages = [row.result for row in logs if row.section == "tick"]
    assert messages.count("tick-usage-90") == 1
    assert messages.count("tick-over-limit") == 1


@pytest.mark.asyncio
async def test_tick_meter_log_write_failure_does_not_add_log_tick(monkeypatch, tmp_path):
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
                    "sections": {"control-plane/tick": {"max-tick-limit": 100, "overlimit-mode": "alert-only"}}
                }
            },
        },
    )

    async def _raise(*args, **kwargs):
        raise RuntimeError("system log unavailable")

    monkeypatch.setattr(storage, "insert_system_log", _raise)
    meter = TickMeter(storage)
    await meter.consume(104, "work", amount=90, stoppable=False)
    await meter.consume(104, "work", amount=20, stoppable=False)

    status = await meter.get_status(104)
    assert status["used"] == 110
    sources = {row["source"]: row["used"] for row in status["sources"]}
    assert "log.system.write" not in sources


@pytest.mark.asyncio
async def test_tick_meter_archives_history_on_minute_rollover(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()
    meter = TickMeter(storage)

    minute = {"value": "2026-01-01T00:00Z"}

    def fake_minute_key(_now: datetime) -> str:
        return minute["value"]

    monkeypatch.setattr(meter_module, "_minute_key", fake_minute_key)
    await meter.consume(104, "command.get", amount=10, stoppable=False)
    minute["value"] = "2026-01-01T00:01Z"
    await meter.consume(104, "command.get", amount=1, stoppable=False)
    status = await meter.get_status(104)
    assert status["used"] == 1
    assert status["history"]
    assert status["history"][0]["minute"] == "2026-01-01T00:00Z"
    assert status["history"][0]["used"] == 10
