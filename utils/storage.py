from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiosqlite


@dataclass
class StoredConfig:
    data: dict[str, Any]
    version: int
    updated_at: str


@dataclass
class HealthResult:
    ok: bool
    backend: str
    detail: str


@dataclass
class LogRow:
    log_id: int
    at: str
    actor_user_id: int | None
    scope_id: int
    section: str
    action: str
    result: str


@dataclass
class CrashLogRow:
    error_id: str
    at: str
    scope_type: str
    scope_id: int
    actor_user_id: int | None
    section: str
    command: str
    message: str
    traceback: str
    context_json: dict[str, Any]
    forward_mode: str
    forward_status: str


@dataclass
class ReceiveConfig:
    receive_mode: str
    crashlog_report_channel: int | None
    crashlog_max_buffer: int


class Storage:
    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL")
        self.backend = "postgres" if self.database_url else "sqlite"
        self._sqlite_path = Path("data/stella.db")
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._pg_pool = None

    async def init_schema(self) -> None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS configs (
                        scope_type TEXT NOT NULL,
                        scope_id BIGINT NOT NULL,
                        section TEXT NOT NULL,
                        data_json JSONB NOT NULL,
                        version INT NOT NULL DEFAULT 1,
                        updated_at TIMESTAMPTZ NOT NULL,
                        PRIMARY KEY(scope_type, scope_id, section)
                    );
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id BIGSERIAL PRIMARY KEY,
                        at TIMESTAMPTZ NOT NULL,
                        actor_user_id BIGINT,
                        scope_type TEXT NOT NULL,
                        scope_id BIGINT NOT NULL,
                        section TEXT NOT NULL,
                        action TEXT NOT NULL,
                        before_json JSONB,
                        after_json JSONB,
                        result TEXT NOT NULL
                    );
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS system_logs (
                        id BIGSERIAL PRIMARY KEY,
                        at TIMESTAMPTZ NOT NULL,
                        actor_user_id BIGINT,
                        scope_id BIGINT NOT NULL,
                        feature TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        message TEXT NOT NULL,
                        detail_json JSONB
                    );
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS crash_logs (
                        error_id TEXT PRIMARY KEY,
                        at TIMESTAMPTZ NOT NULL,
                        scope_type TEXT NOT NULL,
                        scope_id BIGINT NOT NULL,
                        actor_user_id BIGINT,
                        section TEXT NOT NULL,
                        command TEXT NOT NULL,
                        message TEXT NOT NULL,
                        traceback TEXT NOT NULL,
                        context_json JSONB NOT NULL,
                        forward_mode TEXT NOT NULL,
                        forward_status TEXT NOT NULL
                    );
                    """
                )
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS configs (
                    scope_type TEXT NOT NULL,
                    scope_id INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(scope_type, scope_id, section)
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    actor_user_id INTEGER,
                    scope_type TEXT NOT NULL,
                    scope_id INTEGER NOT NULL,
                    section TEXT NOT NULL,
                    action TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    result TEXT NOT NULL
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS system_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    at TEXT NOT NULL,
                    actor_user_id INTEGER,
                    scope_id INTEGER NOT NULL,
                    feature TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail_json TEXT
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS crash_logs (
                    error_id TEXT PRIMARY KEY,
                    at TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_id INTEGER NOT NULL,
                    actor_user_id INTEGER,
                    section TEXT NOT NULL,
                    command TEXT NOT NULL,
                    message TEXT NOT NULL,
                    traceback TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    forward_mode TEXT NOT NULL,
                    forward_status TEXT NOT NULL
                );
                """
            )
            await db.commit()

    async def load_config(self, scope_type: str, scope_id: int, section: str) -> StoredConfig | None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT data_json, version, updated_at FROM configs WHERE scope_type=$1 AND scope_id=$2 AND section=$3",
                    scope_type,
                    scope_id,
                    section,
                )
            if not row:
                return None
            return StoredConfig(data=dict(row["data_json"]), version=row["version"], updated_at=row["updated_at"].isoformat())

        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                "SELECT data_json, version, updated_at FROM configs WHERE scope_type=? AND scope_id=? AND section=?",
                (scope_type, scope_id, section),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return StoredConfig(data=json.loads(row[0]), version=row[1], updated_at=row[2])

    async def upsert_config(self, scope_type: str, scope_id: int, section: str, data: dict[str, Any]) -> int:
        now = datetime.now(timezone.utc).isoformat()

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                version_row = await conn.fetchrow(
                    "SELECT version FROM configs WHERE scope_type=$1 AND scope_id=$2 AND section=$3",
                    scope_type,
                    scope_id,
                    section,
                )
                next_version = (version_row["version"] + 1) if version_row else 1
                await conn.execute(
                    """
                    INSERT INTO configs(scope_type, scope_id, section, data_json, version, updated_at)
                    VALUES($1, $2, $3, $4::jsonb, $5, $6)
                    ON CONFLICT(scope_type, scope_id, section)
                    DO UPDATE SET data_json=EXCLUDED.data_json, version=EXCLUDED.version, updated_at=EXCLUDED.updated_at
                    """,
                    scope_type,
                    scope_id,
                    section,
                    json.dumps(data),
                    next_version,
                    now,
                )
                return next_version

        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                "SELECT version FROM configs WHERE scope_type=? AND scope_id=? AND section=?",
                (scope_type, scope_id, section),
            )
            row = await cursor.fetchone()
            next_version = (row[0] + 1) if row else 1
            await db.execute(
                """
                INSERT INTO configs(scope_type, scope_id, section, data_json, version, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id, section)
                DO UPDATE SET data_json=excluded.data_json, version=excluded.version, updated_at=excluded.updated_at
                """,
                (scope_type, scope_id, section, json.dumps(data), next_version, now),
            )
            await db.commit()
            return next_version

    async def insert_audit_log(
        self,
        actor_user_id: int | None,
        scope_type: str,
        scope_id: int,
        section: str,
        action: str,
        before_json: dict[str, Any] | None,
        after_json: dict[str, Any] | None,
        result: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_logs(at, actor_user_id, scope_type, scope_id, section, action, before_json, after_json, result)
                    VALUES($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9)
                    """,
                    now,
                    actor_user_id,
                    scope_type,
                    scope_id,
                    section,
                    action,
                    json.dumps(before_json) if before_json is not None else None,
                    json.dumps(after_json) if after_json is not None else None,
                    result,
                )
                return

        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO audit_logs(at, actor_user_id, scope_type, scope_id, section, action, before_json, after_json, result)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    actor_user_id,
                    scope_type,
                    scope_id,
                    section,
                    action,
                    json.dumps(before_json) if before_json is not None else None,
                    json.dumps(after_json) if after_json is not None else None,
                    result,
                ),
            )
            await db.commit()

    async def insert_system_log(
        self,
        actor_user_id: int | None,
        scope_id: int,
        feature: str,
        severity: str,
        message: str,
        detail_json: dict[str, Any] | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO system_logs(at, actor_user_id, scope_id, feature, severity, message, detail_json)
                    VALUES($1, $2, $3, $4, $5, $6, $7::jsonb)
                    """,
                    now,
                    actor_user_id,
                    scope_id,
                    feature,
                    severity,
                    message,
                    json.dumps(detail_json) if detail_json is not None else None,
                )
                return

        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                INSERT INTO system_logs(at, actor_user_id, scope_id, feature, severity, message, detail_json)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    actor_user_id,
                    scope_id,
                    feature,
                    severity,
                    message,
                    json.dumps(detail_json) if detail_json is not None else None,
                ),
            )
            await db.commit()

    async def fetch_logs(self, kind: Literal["audit", "system"], scope_id: int, limit: int) -> list[LogRow]:
        limit = max(1, min(limit, 1000))
        if kind == "audit":
            query = "SELECT id, at, actor_user_id, scope_id, section, action, result FROM audit_logs WHERE scope_id={} ORDER BY id DESC LIMIT {}"
        else:
            query = "SELECT id, at, actor_user_id, scope_id, feature as section, severity as action, message as result FROM system_logs WHERE scope_id={} ORDER BY id DESC LIMIT {}"

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(query.format("$1", "$2"), scope_id, limit)
            return [
                LogRow(
                    log_id=row["id"],
                    at=row["at"].isoformat() if hasattr(row["at"], "isoformat") else str(row["at"]),
                    actor_user_id=row["actor_user_id"],
                    scope_id=row["scope_id"],
                    section=row["section"],
                    action=row["action"],
                    result=row["result"],
                )
                for row in rows
            ]

        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(query.format("?", "?"), (scope_id, limit))
            rows = await cursor.fetchall()
        return [
            LogRow(
                log_id=row[0],
                at=row[1],
                actor_user_id=row[2],
                scope_id=row[3],
                section=row[4],
                action=row[5],
                result=row[6],
            )
            for row in rows
        ]

    async def trim_logs(self, scope_id: int, audit_max: int, system_max: int) -> None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    DELETE FROM audit_logs
                    WHERE scope_id=$1
                      AND id NOT IN (
                          SELECT id FROM audit_logs WHERE scope_id=$1 ORDER BY id DESC LIMIT $2
                      )
                    """,
                    scope_id,
                    audit_max,
                )
                await conn.execute(
                    """
                    DELETE FROM system_logs
                    WHERE scope_id=$1
                      AND id NOT IN (
                          SELECT id FROM system_logs WHERE scope_id=$1 ORDER BY id DESC LIMIT $2
                      )
                    """,
                    scope_id,
                    system_max,
                )
                return

        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                DELETE FROM audit_logs
                WHERE scope_id=?
                  AND id NOT IN (
                      SELECT id FROM audit_logs WHERE scope_id=? ORDER BY id DESC LIMIT ?
                  )
                """,
                (scope_id, scope_id, audit_max),
            )
            await db.execute(
                """
                DELETE FROM system_logs
                WHERE scope_id=?
                  AND id NOT IN (
                      SELECT id FROM system_logs WHERE scope_id=? ORDER BY id DESC LIMIT ?
                  )
                """,
                (scope_id, scope_id, system_max),
            )
            await db.commit()

    async def insert_crash_log(
        self,
        scope_type: str,
        scope_id: int,
        actor_user_id: int | None,
        section: str,
        command: str,
        message: str,
        traceback_text: str,
        context_json: dict[str, Any],
        forward_mode: str,
        forward_status: str,
        error_id: str | None = None,
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        crash_id = error_id or self._new_error_id()

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO crash_logs(error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status)
                    VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12)
                    """,
                    crash_id,
                    now,
                    scope_type,
                    scope_id,
                    actor_user_id,
                    section,
                    command,
                    message,
                    traceback_text,
                    json.dumps(context_json),
                    forward_mode,
                    forward_status,
                )
        else:
            async with aiosqlite.connect(self._sqlite_path) as db:
                await db.execute(
                    """
                    INSERT INTO crash_logs(error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        crash_id,
                        now,
                        scope_type,
                        scope_id,
                        actor_user_id,
                        section,
                        command,
                        message,
                        traceback_text,
                        json.dumps(context_json),
                        forward_mode,
                        forward_status,
                    ),
                )
                await db.commit()

        return crash_id

    async def insert_root_crash_copy(self, source_error_id: str, scope_id: int, context_json: dict[str, Any]) -> str:
        return await self.insert_crash_log(
            scope_type="root",
            scope_id=scope_id,
            actor_user_id=None,
            section=context_json.get("section", "unknown"),
            command=context_json.get("command", "unknown"),
            message=f"root-received from {source_error_id}",
            traceback_text=context_json.get("traceback", ""),
            context_json=context_json,
            forward_mode="database",
            forward_status="stored",
        )

    async def fetch_crash_logs(self, scope_type: str, scope_id: int, limit: int) -> list[CrashLogRow]:
        limit = max(1, min(limit, 1000))
        query = (
            "SELECT error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status "
            "FROM crash_logs WHERE scope_type={} AND scope_id={} ORDER BY at DESC LIMIT {}"
        )

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(query.format("$1", "$2", "$3"), scope_type, scope_id, limit)
            return [
                CrashLogRow(
                    error_id=row["error_id"],
                    at=row["at"].isoformat() if hasattr(row["at"], "isoformat") else str(row["at"]),
                    scope_type=row["scope_type"],
                    scope_id=row["scope_id"],
                    actor_user_id=row["actor_user_id"],
                    section=row["section"],
                    command=row["command"],
                    message=row["message"],
                    traceback=row["traceback"],
                    context_json=dict(row["context_json"]),
                    forward_mode=row["forward_mode"],
                    forward_status=row["forward_status"],
                )
                for row in rows
            ]

        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(query.format("?", "?", "?"), (scope_type, scope_id, limit))
            rows = await cursor.fetchall()

        return [
            CrashLogRow(
                error_id=row[0],
                at=row[1],
                scope_type=row[2],
                scope_id=row[3],
                actor_user_id=row[4],
                section=row[5],
                command=row[6],
                message=row[7],
                traceback=row[8],
                context_json=json.loads(row[9]),
                forward_mode=row[10],
                forward_status=row[11],
            )
            for row in rows
        ]

    async def fetch_crash_log_by_error_id(self, scope_type: str, scope_id: int, error_id: str) -> CrashLogRow | None:
        query = (
            "SELECT error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status "
            "FROM crash_logs WHERE scope_type={} AND scope_id={} AND error_id={}"
        )

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(query.format("$1", "$2", "$3"), scope_type, scope_id, error_id)
            if not row:
                return None
            return CrashLogRow(
                error_id=row["error_id"],
                at=row["at"].isoformat() if hasattr(row["at"], "isoformat") else str(row["at"]),
                scope_type=row["scope_type"],
                scope_id=row["scope_id"],
                actor_user_id=row["actor_user_id"],
                section=row["section"],
                command=row["command"],
                message=row["message"],
                traceback=row["traceback"],
                context_json=dict(row["context_json"]),
                forward_mode=row["forward_mode"],
                forward_status=row["forward_status"],
            )

        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(query.format("?", "?", "?"), (scope_type, scope_id, error_id))
            row = await cursor.fetchone()
        if not row:
            return None
        return CrashLogRow(
            error_id=row[0],
            at=row[1],
            scope_type=row[2],
            scope_id=row[3],
            actor_user_id=row[4],
            section=row[5],
            command=row[6],
            message=row[7],
            traceback=row[8],
            context_json=json.loads(row[9]),
            forward_mode=row[10],
            forward_status=row[11],
        )

    async def trim_crash_logs(self, scope_type: str, scope_id: int, max_count: int) -> None:
        max_count = max(1, max_count)
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    DELETE FROM crash_logs
                    WHERE scope_type=$1 AND scope_id=$2
                      AND error_id NOT IN (
                        SELECT error_id FROM crash_logs WHERE scope_type=$1 AND scope_id=$2 ORDER BY at DESC LIMIT $3
                      )
                    """,
                    scope_type,
                    scope_id,
                    max_count,
                )
                return

        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                DELETE FROM crash_logs
                WHERE scope_type=? AND scope_id=?
                  AND error_id NOT IN (
                    SELECT error_id FROM crash_logs WHERE scope_type=? AND scope_id=? ORDER BY at DESC LIMIT ?
                  )
                """,
                (scope_type, scope_id, scope_type, scope_id, max_count),
            )
            await db.commit()

    async def resolve_receive_config(self) -> ReceiveConfig:
        stored = await self.load_config("root", 0, "tenant-connection")
        if not stored:
            return ReceiveConfig(receive_mode="off", crashlog_report_channel=None, crashlog_max_buffer=500)

        raw = stored.data
        payload = raw.get("payload") if isinstance(raw, dict) else None
        if not isinstance(payload, dict):
            payload = raw if isinstance(raw, dict) else {}

        log_data = payload.get("log", {}) if isinstance(payload.get("log", {}), dict) else {}
        mode = str(log_data.get("receive_mode", "off"))
        channel_raw = log_data.get("crashlog_report_channel")
        channel = channel_raw if isinstance(channel_raw, int) else None
        buffer_raw = log_data.get("crashlog_max_buffer", 500)
        buffer_val = buffer_raw if isinstance(buffer_raw, int) else 500
        return ReceiveConfig(receive_mode=mode, crashlog_report_channel=channel, crashlog_max_buffer=buffer_val)

    async def healthcheck(self) -> HealthResult:
        try:
            if self.backend == "postgres":
                await self._ensure_pg_pool()
                async with self._pg_pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                return HealthResult(ok=True, backend="postgres", detail="ok")

            async with aiosqlite.connect(self._sqlite_path) as db:
                await db.execute("SELECT 1")
            return HealthResult(ok=True, backend="sqlite", detail=str(self._sqlite_path))
        except Exception as exc:
            return HealthResult(ok=False, backend=self.backend, detail=str(exc))

    async def _ensure_pg_pool(self) -> None:
        if self._pg_pool is not None:
            return
        import asyncpg

        self._pg_pool = await asyncpg.create_pool(self.database_url)

    def _new_error_id(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        return f"CR-{now}-{suffix}"
