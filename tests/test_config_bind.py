from __future__ import annotations

import json
import importlib
import sys

import aiosqlite
import pytest
from discord.ext import commands

from utils import config_bind
from utils.storage import Storage


async def _fetch_bind_failed_rows(storage: Storage) -> list[tuple[int, dict]]:
    async with aiosqlite.connect(storage._sqlite_path) as db:  # noqa: SLF001
        await storage._ensure_sqlite_tenant_tables("root", 0, db=db)  # noqa: SLF001
    parsed: list[tuple[int, dict]] = []
    for scope_id in [0, 11, 22, 33, 77]:
        scope_type = "root" if scope_id == 0 else "guild"
        async with aiosqlite.connect(storage._sqlite_path) as db:  # noqa: SLF001
            await storage._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)  # noqa: SLF001
        tenant_key = storage._tenant_key(scope_type, scope_id)  # noqa: SLF001
        table = storage._table_name(tenant_key, "system_logs")  # noqa: SLF001
        async with aiosqlite.connect(storage._sqlite_path) as db:  # noqa: SLF001
            exists_cursor = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if await exists_cursor.fetchone() is None:
                continue
            cursor = await db.execute(
                f"SELECT scope_id, detail_json FROM {table} WHERE message=? ORDER BY id ASC",
                ("bind-failed",),
            )
            rows = await cursor.fetchall()
        for row_scope_id, detail_json in rows:
            parsed.append((int(row_scope_id), json.loads(detail_json) if detail_json else {}))
    return parsed


@pytest.mark.asyncio
async def test_bind_all_settings_logs_root_failure_details(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def fail_root(_engine):
        raise RuntimeError("boom-root")

    monkeypatch.setattr(config_bind, "_bind_root_sections", fail_root)

    with pytest.raises(RuntimeError, match="boom-root"):
        await config_bind.bind_all_settings(storage, [11, 22], tick_meter=None)

    failed_rows = await _fetch_bind_failed_rows(storage)
    assert failed_rows
    root_scope, root_detail = failed_rows[0]
    assert root_scope == 0
    assert root_detail["phase"] == "root-bind"
    assert root_detail["error_type"] == "RuntimeError"
    assert root_detail["error_message"] == "boom-root"
    assert "traceback" in root_detail

    guild_rows = failed_rows[1:]
    assert len(guild_rows) == 2
    for scope_id, detail in guild_rows:
        assert scope_id in {11, 22}
        assert detail["status"] == "skipped_due_to_global_failure"
        assert detail["phase"] == "root-bind"


@pytest.mark.asyncio
async def test_bind_all_settings_logs_failed_and_skipped_guild_details(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def ok_root(_engine):
        return None

    async def fail_guild(_engine, guild_id: int):
        if guild_id == 22:
            raise ValueError("boom-guild")
        return None

    monkeypatch.setattr(config_bind, "_bind_root_sections", ok_root)
    monkeypatch.setattr(config_bind, "_bind_guild_sections", fail_guild)

    with pytest.raises(ValueError, match="boom-guild"):
        await config_bind.bind_all_settings(storage, [11, 22, 33], tick_meter=None)

    failed_rows = await _fetch_bind_failed_rows(storage)
    assert failed_rows
    root_scope, root_detail = failed_rows[0]
    assert root_scope == 0
    assert root_detail["phase"] == "guild-bind"
    assert root_detail["failed_guild_id"] == 22
    assert root_detail["error_type"] == "ValueError"

    details_by_scope = {scope: detail for scope, detail in failed_rows[1:]}
    assert 11 not in details_by_scope
    assert details_by_scope[22]["status"] == "failed_current_guild"
    assert details_by_scope[22]["failed_guild_id"] == 22
    assert details_by_scope[33]["status"] == "skipped_due_to_global_failure"


@pytest.mark.asyncio
async def test_bind_single_guild_logs_failure_details(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    storage = Storage()
    await storage.init_schema()

    async def fail_root(_engine):
        raise RuntimeError("single-root-fail")

    monkeypatch.setattr(config_bind, "_bind_root_sections", fail_root)

    with pytest.raises(RuntimeError, match="single-root-fail"):
        await config_bind.bind_single_guild(storage, 77)

    failed_rows = await _fetch_bind_failed_rows(storage)
    assert len(failed_rows) == 1
    scope_id, detail = failed_rows[0]
    assert scope_id == 77
    assert detail["guild_id"] == 77
    assert detail["phase"] == "root-bind"
    assert detail["error_type"] == "RuntimeError"
    assert detail["error_message"] == "single-root-fail"
    assert "traceback" in detail


def test_root_diff_sections_collects_global_and_override_keys():
    defaults = {"sections": {"welcome": {"welcome-message": "x"}}}
    enforce = {"sections": {"control-plane/tick": {"max-tick-limit": 5000}}}
    override = {
        "guilds": {
            "22": {"sections": {"sticky-message": {"message": "x"}}},
            "33": {"sections": {"auto-reaction": {"enabled": True}}},
        }
    }
    sections = config_bind.root_diff_sections(defaults, enforce, override, 22)
    assert "welcome" in sections
    assert "control-plane/tick" in sections
    assert "sticky-message" in sections
    assert "auto-reaction" not in sections


@pytest.mark.asyncio
async def test_bot_config_bind_failure_releases_waiters_and_retries(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOKEN", "test-token")
    monkeypatch.setattr(commands.Bot, "run", lambda self, token: None)
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    calls = 0

    async def flaky_bind(_storage, _guild_ids, tick_meter=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("bind failed once")

    monkeypatch.setattr(main_module, "bind_all_settings", flaky_bind)

    await main_module.bot.ensure_config_bound()
    assert main_module.bot.config_bind_ready.is_set()
    assert isinstance(main_module.bot.config_bind_error, RuntimeError)
    assert main_module.bot._config_bind_started is False  # noqa: SLF001

    await main_module.bot.ensure_config_bound()
    assert calls == 2
    assert main_module.bot.config_bind_ready.is_set()
    assert main_module.bot.config_bind_error is None


@pytest.mark.asyncio
async def test_bot_setup_hook_does_not_require_logged_in_user(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOKEN", "test-token")
    monkeypatch.setattr(commands.Bot, "run", lambda self, token: None)
    sys.modules.pop("main", None)
    main_module = importlib.import_module("main")

    assert main_module.bot.user is None
    await main_module.bot.setup_hook()
    assert main_module.bot.ready_check is True
