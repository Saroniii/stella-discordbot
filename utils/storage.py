from __future__ import annotations

import json
import os
import re
import uuid
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiosqlite

logger = logging.getLogger(__name__)


def _row_value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (TypeError, KeyError, IndexError):
        return row[index]


def _row_datetime_text(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


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


@dataclass
class LevelTableRow:
    level: int
    required_total_xp: int
    delta_xp: int
    segment: str
    rebuilt_at: str


@dataclass
class LevelUserRow:
    guild_id: int
    user_id: int
    total_xp: int
    level: int
    updated_at: str


@dataclass
class LevelRuntimeRow:
    guild_id: int
    user_id: int
    last_message_at: str | None
    last_reaction_at: str | None
    voice_joined_at: str | None
    last_voice_grant_at: str | None


@dataclass
class TickConfig:
    max_tick_limit: int
    overlimit_mode: str


@dataclass
class UtilityWebhookRow:
    ref_id: str
    guild_id: int
    channel_id: int
    webhook_id: int
    webhook_token: str
    tag: str
    created_at: str


@dataclass
class StickyRuntimeRow:
    guild_id: int
    channel_id: int
    message_id: int
    signature: str | None
    updated_at: str


@dataclass
class ChatGroupRow:
    group_id: str
    name: str
    mode: str
    join_need_apply: bool
    status: str
    leader_guild_id: int
    rate_limit: int
    overlimit_mode: str
    slowmode_sec: int
    retention_days: int
    created_at: str
    updated_at: str


@dataclass
class ChatGroupMembershipRow:
    group_id: str
    guild_id: int
    status: str
    role: str
    joined_at: str


@dataclass
class ChatGroupRateLimitStateRow:
    group_id: str
    inflight_count: int
    queued_count: int
    updated_at: str


@dataclass
class ChatGroupConnectionRow:
    group_id: str
    guild_id: int
    channel_id: int
    webhook_ref: str | None
    name_format: str


@dataclass
class ChatGroupApplicationRow:
    apply_id: str
    group_id: str
    guild_id: int
    channel_id: int
    status: str
    requested_at: str
    decided_at: str | None
    decided_by: int | None


@dataclass
class ChatGroupAuthKeyRow:
    id: int
    group_id: str
    guild_id: int | None
    key_preview: str
    status: str
    created_at: str
    revoked_at: str | None


@dataclass
class ChatGroupMessageRow:
    message_id: int
    group_id: str
    source_guild_id: int
    source_channel_id: int
    source_message_id: int
    author_user_id: int
    author_name: str
    content: str
    attachment_urls: list[str]
    created_at: str
    deleted: bool


class Storage:
    _TENANT_TABLES: tuple[str, ...] = (
        "configs",
        "audit_logs",
        "system_logs",
        "crash_logs",
        "level_tables",
        "level_users",
        "level_runtime",
        "level_event_logs",
        "utility_webhooks",
        "sticky_runtime",
    )

    def __init__(self) -> None:
        self.database_url = os.getenv("DATABASE_URL")
        self.backend = "postgres" if self.database_url else "sqlite"
        self._sqlite_path = Path("data/stella.db")
        self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self._pg_pool = None

    def _utility_webhook_row(self, row: Any) -> UtilityWebhookRow:
        return UtilityWebhookRow(
            ref_id=str(_row_value(row, "ref_id", 0)),
            guild_id=int(_row_value(row, "guild_id", 1)),
            channel_id=int(_row_value(row, "channel_id", 2)),
            webhook_id=int(_row_value(row, "webhook_id", 3)),
            webhook_token=str(_row_value(row, "webhook_token", 4)),
            tag=str(_row_value(row, "tag", 5)),
            created_at=_row_datetime_text(_row_value(row, "created_at", 6)),
        )

    async def init_schema(self) -> None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tenant_registry (
                        tenant_key TEXT PRIMARY KEY,
                        scope_type TEXT NOT NULL,
                        scope_id BIGINT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        last_seen_at TIMESTAMPTZ NOT NULL,
                        schema_version INT NOT NULL DEFAULT 1,
                        migration_phase TEXT NOT NULL DEFAULT 'cutover'
                    );
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS utility_webhook_index (
                        ref_id TEXT PRIMARY KEY,
                        tenant_key TEXT NOT NULL,
                        guild_id BIGINT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    );
                    """
                )
                await self._ensure_pg_chat_group_tables(conn)
                await self._ensure_pg_tenant_tables("root", 0, conn=conn)
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tenant_registry (
                    tenant_key TEXT PRIMARY KEY,
                    scope_type TEXT NOT NULL,
                    scope_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    migration_phase TEXT NOT NULL DEFAULT 'cutover'
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS utility_webhook_index (
                    ref_id TEXT PRIMARY KEY,
                    tenant_key TEXT NOT NULL,
                    guild_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            await self._ensure_sqlite_chat_group_tables(db)
            await self._ensure_sqlite_tenant_tables("root", 0, db=db)
            await db.commit()

    async def load_config(self, scope_type: str, scope_id: int, section: str) -> StoredConfig | None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, scope_id, conn=conn), "configs")
                row = await conn.fetchrow(
                    f"SELECT data_json, version, updated_at FROM {table} WHERE scope_type=$1 AND scope_id=$2 AND section=$3",
                    scope_type,
                    scope_id,
                    section,
                )
            if not row:
                return None
            return StoredConfig(data=dict(row["data_json"]), version=row["version"], updated_at=row["updated_at"].isoformat())

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
            table = self._table_name(tenant_key, "configs")
            cursor = await db.execute(
                f"SELECT data_json, version, updated_at FROM {table} WHERE scope_type=? AND scope_id=? AND section=?",
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
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, scope_id, conn=conn), "configs")
                version_row = await conn.fetchrow(
                    f"SELECT version FROM {table} WHERE scope_type=$1 AND scope_id=$2 AND section=$3",
                    scope_type,
                    scope_id,
                    section,
                )
                next_version = (version_row["version"] + 1) if version_row else 1
                await conn.execute(
                    f"""
                    INSERT INTO {table}(scope_type, scope_id, section, data_json, version, updated_at)
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
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
            table = self._table_name(tenant_key, "configs")
            cursor = await db.execute(
                f"SELECT version FROM {table} WHERE scope_type=? AND scope_id=? AND section=?",
                (scope_type, scope_id, section),
            )
            row = await cursor.fetchone()
            next_version = (row[0] + 1) if row else 1
            await db.execute(
                f"""
                INSERT INTO configs(scope_type, scope_id, section, data_json, version, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id, section)
                DO UPDATE SET data_json=excluded.data_json, version=excluded.version, updated_at=excluded.updated_at
                """.replace("configs", table),
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
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, scope_id, conn=conn), "audit_logs")
                await conn.execute(
                    f"""
                    INSERT INTO {table}(at, actor_user_id, scope_type, scope_id, section, action, before_json, after_json, result)
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
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
            table = self._table_name(tenant_key, "audit_logs")
            await db.execute(
                f"""
                INSERT INTO audit_logs(at, actor_user_id, scope_type, scope_id, section, action, before_json, after_json, result)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """.replace("audit_logs", table),
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

    async def insert_audit_log_safe(
        self,
        actor_user_id: int | None,
        scope_type: str,
        scope_id: int,
        section: str,
        action: str,
        before_json: dict[str, Any] | None,
        after_json: dict[str, Any] | None,
        result: str,
    ) -> bool:
        """Fail-safe wrapper for audit log write.

        Returns:
            bool: True when written successfully, False when a storage error occurred.
        """
        try:
            await self.insert_audit_log(
                actor_user_id=actor_user_id,
                scope_type=scope_type,
                scope_id=scope_id,
                section=section,
                action=action,
                before_json=before_json,
                after_json=after_json,
                result=result,
            )
            return True
        except Exception:
            logger.warning("audit log write failed: section=%s action=%s", section, action, exc_info=True)
            return False

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
                scope_type, resolved_scope_id = self._scope_from_scope_id(scope_id)
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, resolved_scope_id, conn=conn), "system_logs")
                await conn.execute(
                    f"""
                    INSERT INTO {table}(at, actor_user_id, scope_id, feature, severity, message, detail_json)
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

        scope_type, resolved_scope_id = self._scope_from_scope_id(scope_id)
        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, resolved_scope_id, db=db)
            table = self._table_name(tenant_key, "system_logs")
            await db.execute(
                f"""
                INSERT INTO system_logs(at, actor_user_id, scope_id, feature, severity, message, detail_json)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """.replace("system_logs", table),
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

    async def insert_system_log_safe(
        self,
        actor_user_id: int | None,
        scope_id: int,
        feature: str,
        severity: str,
        message: str,
        detail_json: dict[str, Any] | None,
    ) -> bool:
        """Fail-safe wrapper for system log write.

        Returns:
            bool: True when written successfully, False when a storage error occurred.
        """
        try:
            await self.insert_system_log(
                actor_user_id=actor_user_id,
                scope_id=scope_id,
                feature=feature,
                severity=severity,
                message=message,
                detail_json=detail_json,
            )
            return True
        except Exception:
            logger.warning("system log write failed: feature=%s message=%s", feature, message, exc_info=True)
            return False

    async def fetch_logs(self, kind: Literal["audit", "system"], scope_id: int, limit: int) -> list[LogRow]:
        limit = max(1, min(limit, 1000))
        if kind == "audit":
            query = "SELECT id, at, actor_user_id, scope_id, section, action, result FROM {table} WHERE scope_id=? ORDER BY id DESC LIMIT ?"
            logical = "audit_logs"
        else:
            query = "SELECT id, at, actor_user_id, scope_id, feature as section, severity as action, message as result FROM {table} WHERE scope_id=? ORDER BY id DESC LIMIT ?"
            logical = "system_logs"

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                scope_type, resolved_scope_id = self._scope_from_scope_id(scope_id)
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, resolved_scope_id, conn=conn), logical)
                rows = await conn.fetch(query.format(table=table).replace("?", "$1", 1).replace("?", "$2", 1), scope_id, limit)
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

        scope_type, resolved_scope_id = self._scope_from_scope_id(scope_id)
        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, resolved_scope_id, db=db)
            table = self._table_name(tenant_key, logical)
            cursor = await db.execute(query.format(table=table), (scope_id, limit))
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

    async def fetch_logs_safe(self, kind: Literal["audit", "system"], scope_id: int, limit: int) -> list[LogRow]:
        """Fail-safe wrapper for log reads.

        Returns empty list on storage errors.
        """
        try:
            return await self.fetch_logs(kind, scope_id, limit)
        except Exception:
            logger.warning("log fetch failed: kind=%s scope_id=%s", kind, scope_id, exc_info=True)
            return []

    async def trim_logs(self, scope_id: int, audit_max: int, system_max: int) -> None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                scope_type, resolved_scope_id = self._scope_from_scope_id(scope_id)
                tenant_key = await self._ensure_pg_tenant_tables(scope_type, resolved_scope_id, conn=conn)
                audit_table = self._table_name(tenant_key, "audit_logs")
                system_table = self._table_name(tenant_key, "system_logs")
                await conn.execute(
                    f"""
                    DELETE FROM {audit_table}
                    WHERE scope_id=$1
                      AND id NOT IN (
                          SELECT id FROM {audit_table} WHERE scope_id=$1 ORDER BY id DESC LIMIT $2
                      )
                    """,
                    scope_id,
                    audit_max,
                )
                await conn.execute(
                    f"""
                    DELETE FROM {system_table}
                    WHERE scope_id=$1
                      AND id NOT IN (
                          SELECT id FROM {system_table} WHERE scope_id=$1 ORDER BY id DESC LIMIT $2
                      )
                    """,
                    scope_id,
                    system_max,
                )
                return

        scope_type, resolved_scope_id = self._scope_from_scope_id(scope_id)
        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, resolved_scope_id, db=db)
            audit_table = self._table_name(tenant_key, "audit_logs")
            system_table = self._table_name(tenant_key, "system_logs")
            await db.execute(
                f"""
                DELETE FROM audit_logs
                WHERE scope_id=?
                  AND id NOT IN (
                      SELECT id FROM audit_logs WHERE scope_id=? ORDER BY id DESC LIMIT ?
                  )
                """.replace("audit_logs", audit_table),
                (scope_id, scope_id, audit_max),
            )
            await db.execute(
                f"""
                DELETE FROM system_logs
                WHERE scope_id=?
                  AND id NOT IN (
                      SELECT id FROM system_logs WHERE scope_id=? ORDER BY id DESC LIMIT ?
                  )
                """.replace("system_logs", system_table),
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
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, scope_id, conn=conn), "crash_logs")
                await conn.execute(
                    f"""
                    INSERT INTO {table}(error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status)
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
                tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
                table = self._table_name(tenant_key, "crash_logs")
                await db.execute(
                    f"""
                    INSERT INTO crash_logs(error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """.replace("crash_logs", table),
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

    async def insert_crash_log_safe(
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
    ) -> tuple[bool, str]:
        """Fail-safe wrapper for crash log write.

        Returns:
            tuple[bool, str]: (success flag, persisted or fallback error_id)
        """
        try:
            crash_id = await self.insert_crash_log(
                scope_type=scope_type,
                scope_id=scope_id,
                actor_user_id=actor_user_id,
                section=section,
                command=command,
                message=message,
                traceback_text=traceback_text,
                context_json=context_json,
                forward_mode=forward_mode,
                forward_status=forward_status,
                error_id=error_id,
            )
            return True, crash_id
        except Exception:
            logger.warning("crash log write failed: scope=%s:%s section=%s", scope_type, scope_id, section, exc_info=True)
            return False, error_id or f"CR-{uuid.uuid4().hex[:8]}"

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
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, scope_id, conn=conn), "crash_logs")
                rows = await conn.fetch(query.replace("crash_logs", table).format("$1", "$2", "$3"), scope_type, scope_id, limit)
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
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
            table = self._table_name(tenant_key, "crash_logs")
            cursor = await db.execute(query.replace("crash_logs", table).format("?", "?", "?"), (scope_type, scope_id, limit))
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

    async def fetch_crash_logs_safe(self, scope_type: str, scope_id: int, limit: int) -> list[CrashLogRow]:
        """Fail-safe wrapper for crash log list reads.

        Returns empty list on storage errors.
        """
        try:
            return await self.fetch_crash_logs(scope_type, scope_id, limit)
        except Exception:
            logger.warning("crash log list fetch failed: scope=%s:%s", scope_type, scope_id, exc_info=True)
            return []

    async def fetch_crash_log_by_error_id(self, scope_type: str, scope_id: int, error_id: str) -> CrashLogRow | None:
        query = (
            "SELECT error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status "
            "FROM crash_logs WHERE scope_type={} AND scope_id={} AND error_id={}"
        )

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, scope_id, conn=conn), "crash_logs")
                row = await conn.fetchrow(query.replace("crash_logs", table).format("$1", "$2", "$3"), scope_type, scope_id, error_id)
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
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
            table = self._table_name(tenant_key, "crash_logs")
            cursor = await db.execute(query.replace("crash_logs", table).format("?", "?", "?"), (scope_type, scope_id, error_id))
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

    async def fetch_crash_log_by_error_id_safe(self, scope_type: str, scope_id: int, error_id: str) -> CrashLogRow | None:
        """Fail-safe wrapper for crash log detail reads.

        Returns None on storage errors.
        """
        try:
            return await self.fetch_crash_log_by_error_id(scope_type, scope_id, error_id)
        except Exception:
            logger.warning(
                "crash log detail fetch failed: scope=%s:%s error_id=%s",
                scope_type,
                scope_id,
                error_id,
                exc_info=True,
            )
            return None

    async def trim_crash_logs(self, scope_type: str, scope_id: int, max_count: int) -> None:
        max_count = max(1, max_count)
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables(scope_type, scope_id, conn=conn), "crash_logs")
                await conn.execute(
                    f"""
                    DELETE FROM {table}
                    WHERE scope_type=$1 AND scope_id=$2
                      AND error_id NOT IN (
                        SELECT error_id FROM {table} WHERE scope_type=$1 AND scope_id=$2 ORDER BY at DESC LIMIT $3
                      )
                    """,
                    scope_type,
                    scope_id,
                    max_count,
                )
                return

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
            table = self._table_name(tenant_key, "crash_logs")
            await db.execute(
                f"""
                DELETE FROM crash_logs
                WHERE scope_type=? AND scope_id=?
                  AND error_id NOT IN (
                    SELECT error_id FROM crash_logs WHERE scope_type=? AND scope_id=? ORDER BY at DESC LIMIT ?
                  )
                """.replace("crash_logs", table),
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

        if "running_payload" in payload and isinstance(payload.get("running_payload"), dict):
            payload = payload["running_payload"]

        log_data = payload.get("log", {}) if isinstance(payload.get("log", {}), dict) else {}
        mode = str(log_data.get("receive_mode", "off"))
        channel_raw = log_data.get("crashlog_report_channel")
        channel = channel_raw if isinstance(channel_raw, int) else None
        buffer_raw = log_data.get("crashlog_max_buffer", 500)
        buffer_val = buffer_raw if isinstance(buffer_raw, int) else 500
        return ReceiveConfig(receive_mode=mode, crashlog_report_channel=channel, crashlog_max_buffer=buffer_val)

    async def resolve_tick_config(self, guild_id: int) -> TickConfig:
        max_tick_limit = 3000
        overlimit_mode = "alert-only"

        guild_override = await self.load_config("guild", guild_id, "control-plane")
        if guild_override:
            payload = self._extract_running_payload(guild_override.data)
            tick_data = payload.get("tick", {}) if isinstance(payload.get("tick"), dict) else {}
            value = tick_data.get("max_tick_limit")
            mode = tick_data.get("overlimit_mode")
            if isinstance(value, int):
                max_tick_limit = value
            if isinstance(mode, str) and mode in {"alert-only", "drop-new-work"}:
                overlimit_mode = mode

        root_enforce = await self.load_config("root", 0, "root-enforce")
        if root_enforce:
            payload = self._extract_running_payload(root_enforce.data)
            sections = payload.get("sections", {})
            if isinstance(sections, dict):
                forced_limit, forced_mode = self._extract_tick_override_from_sections(sections)
                if isinstance(forced_limit, int):
                    max_tick_limit = forced_limit
                if isinstance(forced_mode, str) and forced_mode in {"alert-only", "drop-new-work"}:
                    overlimit_mode = forced_mode

        root_override = await self.load_config("root", 0, "root-enforce-override")
        if root_override:
            payload = self._extract_running_payload(root_override.data)
            guilds = payload.get("guilds", {}) if isinstance(payload, dict) else {}
            entry = guilds.get(str(guild_id), {}) if isinstance(guilds, dict) else {}
            sections = entry.get("sections", {}) if isinstance(entry, dict) else {}
            if isinstance(sections, dict):
                forced_limit, forced_mode = self._extract_tick_override_from_sections(sections)
                if isinstance(forced_limit, int):
                    max_tick_limit = forced_limit
                if isinstance(forced_mode, str) and forced_mode in {"alert-only", "drop-new-work"}:
                    overlimit_mode = forced_mode

        max_tick_limit = max(100, min(max_tick_limit, 1000000))
        return TickConfig(max_tick_limit=max_tick_limit, overlimit_mode=overlimit_mode)

    def _extract_tick_override_from_sections(self, sections: dict[str, Any]) -> tuple[int | None, str | None]:
        forced_limit: int | None = None
        forced_mode: str | None = None

        direct = sections.get("control-plane/tick", {})
        if isinstance(direct, dict):
            value = direct.get("max-tick-limit", direct.get("max_tick_limit"))
            mode = direct.get("overlimit-mode", direct.get("overlimit_mode"))
            if isinstance(value, int):
                forced_limit = value
            if isinstance(mode, str):
                forced_mode = mode

        legacy = sections.get("control-plane", {})
        if isinstance(legacy, dict):
            value = legacy.get("tick.max-tick-limit", legacy.get("tick.max_tick_limit"))
            mode = legacy.get("tick.overlimit-mode", legacy.get("tick.overlimit_mode"))
            if isinstance(value, int):
                forced_limit = value
            if isinstance(mode, str):
                forced_mode = mode

        return forced_limit, forced_mode

    async def is_management_module_enabled(self, guild_id: int, module: str) -> bool:
        normalized = module.replace("-", "_")
        if normalized not in {"welcome", "level", "sticky_message", "auto_reaction"}:
            return True

        stored = await self.load_config("guild", guild_id, "management-module")
        if not stored:
            return True

        raw = stored.data
        payload = raw.get("payload") if isinstance(raw, dict) else None
        if not isinstance(payload, dict):
            payload = raw if isinstance(raw, dict) else {}

        if "running_payload" in payload and isinstance(payload.get("running_payload"), dict):
            payload = payload["running_payload"]

        value = payload.get(normalized)
        if isinstance(value, bool):
            return value
        return True

    async def replace_level_table(self, guild_id: int, rows: list[dict[str, Any]]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "level_tables")
                async with conn.transaction():
                    await conn.execute(f"DELETE FROM {table} WHERE guild_id=$1", guild_id)
                    for row in rows:
                        await conn.execute(
                            f"""
                            INSERT INTO {table}(guild_id, level, required_total_xp, delta_xp, segment, rebuilt_at)
                            VALUES($1, $2, $3, $4, $5, $6)
                            """,
                            guild_id,
                            int(row["level"]),
                            int(row["required_total_xp"]),
                            int(row["delta_xp"]),
                            str(row["segment"]),
                            now,
                        )
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "level_tables")
            await db.execute(f"DELETE FROM {table} WHERE guild_id=?", (guild_id,))
            for row in rows:
                await db.execute(
                    f"""
                    INSERT INTO level_tables(guild_id, level, required_total_xp, delta_xp, segment, rebuilt_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """.replace("level_tables", table),
                    (
                        guild_id,
                        int(row["level"]),
                        int(row["required_total_xp"]),
                        int(row["delta_xp"]),
                        str(row["segment"]),
                        now,
                    ),
                )
            await db.commit()

    async def fetch_level_table(self, guild_id: int, limit: int = 2000) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 10000))
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "level_tables")
                rows = await conn.fetch(
                    f"""
                    SELECT level, required_total_xp, delta_xp, segment, rebuilt_at
                    FROM {table}
                    WHERE guild_id=$1
                    ORDER BY level ASC
                    LIMIT $2
                    """,
                    guild_id,
                    limit,
                )
            return [
                {
                    "level": int(row["level"]),
                    "required_total_xp": int(row["required_total_xp"]),
                    "delta_xp": int(row["delta_xp"]),
                    "segment": str(row["segment"]),
                    "rebuilt_at": row["rebuilt_at"].isoformat() if hasattr(row["rebuilt_at"], "isoformat") else str(row["rebuilt_at"]),
                }
                for row in rows
            ]

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "level_tables")
            cursor = await db.execute(
                f"""
                SELECT level, required_total_xp, delta_xp, segment, rebuilt_at
                FROM level_tables
                WHERE guild_id=?
                ORDER BY level ASC
                LIMIT ?
                """.replace("level_tables", table),
                (guild_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            {
                "level": int(row[0]),
                "required_total_xp": int(row[1]),
                "delta_xp": int(row[2]),
                "segment": str(row[3]),
                "rebuilt_at": str(row[4]),
            }
            for row in rows
        ]

    async def get_level_user(self, guild_id: int, user_id: int) -> LevelUserRow | None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "level_users")
                row = await conn.fetchrow(
                    f"""
                    SELECT guild_id, user_id, total_xp, level, updated_at
                    FROM {table}
                    WHERE guild_id=$1 AND user_id=$2
                    """,
                    guild_id,
                    user_id,
                )
            if row is None:
                return None
            return LevelUserRow(
                guild_id=int(row["guild_id"]),
                user_id=int(row["user_id"]),
                total_xp=int(row["total_xp"]),
                level=int(row["level"]),
                updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
            )

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "level_users")
            cursor = await db.execute(
                f"""
                SELECT guild_id, user_id, total_xp, level, updated_at
                FROM level_users
                WHERE guild_id=? AND user_id=?
                """.replace("level_users", table),
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return LevelUserRow(
            guild_id=int(row[0]),
            user_id=int(row[1]),
            total_xp=int(row[2]),
            level=int(row[3]),
            updated_at=str(row[4]),
        )

    async def upsert_level_user(self, guild_id: int, user_id: int, total_xp: int, level: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "level_users")
                await conn.execute(
                    f"""
                    INSERT INTO {table}(guild_id, user_id, total_xp, level, updated_at)
                    VALUES($1, $2, $3, $4, $5)
                    ON CONFLICT(guild_id, user_id)
                    DO UPDATE SET total_xp=EXCLUDED.total_xp, level=EXCLUDED.level, updated_at=EXCLUDED.updated_at
                    """,
                    guild_id,
                    user_id,
                    int(total_xp),
                    int(level),
                    now,
                )
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "level_users")
            await db.execute(
                f"""
                INSERT INTO level_users(guild_id, user_id, total_xp, level, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET total_xp=excluded.total_xp, level=excluded.level, updated_at=excluded.updated_at
                """.replace("level_users", table),
                (guild_id, user_id, int(total_xp), int(level), now),
            )
            await db.commit()

    async def fetch_level_ranking(self, guild_id: int, limit: int) -> list[LevelUserRow]:
        limit = max(1, min(limit, 100))
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "level_users")
                rows = await conn.fetch(
                    f"""
                    SELECT guild_id, user_id, total_xp, level, updated_at
                    FROM {table}
                    WHERE guild_id=$1
                    ORDER BY total_xp DESC, user_id ASC
                    LIMIT $2
                    """,
                    guild_id,
                    limit,
                )
            return [
                LevelUserRow(
                    guild_id=int(row["guild_id"]),
                    user_id=int(row["user_id"]),
                    total_xp=int(row["total_xp"]),
                    level=int(row["level"]),
                    updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
                )
                for row in rows
            ]

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "level_users")
            cursor = await db.execute(
                f"""
                SELECT guild_id, user_id, total_xp, level, updated_at
                FROM level_users
                WHERE guild_id=?
                ORDER BY total_xp DESC, user_id ASC
                LIMIT ?
                """.replace("level_users", table),
                (guild_id, limit),
            )
            rows = await cursor.fetchall()
        return [
            LevelUserRow(
                guild_id=int(row[0]),
                user_id=int(row[1]),
                total_xp=int(row[2]),
                level=int(row[3]),
                updated_at=str(row[4]),
            )
            for row in rows
        ]

    async def get_level_runtime(self, guild_id: int, user_id: int) -> LevelRuntimeRow:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "level_runtime")
                row = await conn.fetchrow(
                    f"""
                    SELECT guild_id, user_id, last_message_at, last_reaction_at, voice_joined_at, last_voice_grant_at
                    FROM {table}
                    WHERE guild_id=$1 AND user_id=$2
                    """,
                    guild_id,
                    user_id,
                )
            if row is None:
                return LevelRuntimeRow(guild_id=guild_id, user_id=user_id, last_message_at=None, last_reaction_at=None, voice_joined_at=None, last_voice_grant_at=None)
            return LevelRuntimeRow(
                guild_id=int(row["guild_id"]),
                user_id=int(row["user_id"]),
                last_message_at=row["last_message_at"].isoformat() if row["last_message_at"] is not None else None,
                last_reaction_at=row["last_reaction_at"].isoformat() if row["last_reaction_at"] is not None else None,
                voice_joined_at=row["voice_joined_at"].isoformat() if row["voice_joined_at"] is not None else None,
                last_voice_grant_at=row["last_voice_grant_at"].isoformat() if row["last_voice_grant_at"] is not None else None,
            )

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "level_runtime")
            cursor = await db.execute(
                f"""
                SELECT guild_id, user_id, last_message_at, last_reaction_at, voice_joined_at, last_voice_grant_at
                FROM level_runtime
                WHERE guild_id=? AND user_id=?
                """.replace("level_runtime", table),
                (guild_id, user_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return LevelRuntimeRow(guild_id=guild_id, user_id=user_id, last_message_at=None, last_reaction_at=None, voice_joined_at=None, last_voice_grant_at=None)
        return LevelRuntimeRow(
            guild_id=int(row[0]),
            user_id=int(row[1]),
            last_message_at=str(row[2]) if row[2] is not None else None,
            last_reaction_at=str(row[3]) if row[3] is not None else None,
            voice_joined_at=str(row[4]) if row[4] is not None else None,
            last_voice_grant_at=str(row[5]) if row[5] is not None else None,
        )

    async def upsert_level_runtime(
        self,
        guild_id: int,
        user_id: int,
        *,
        last_message_at: str | None = None,
        last_reaction_at: str | None = None,
        voice_joined_at: str | None = None,
        last_voice_grant_at: str | None = None,
        clear_voice_joined_at: bool = False,
        clear_last_message_at: bool = False,
        clear_last_reaction_at: bool = False,
        clear_last_voice_grant_at: bool = False,
    ) -> None:
        current = await self.get_level_runtime(guild_id, user_id)
        new_last_message_at = None if clear_last_message_at else (last_message_at if last_message_at is not None else current.last_message_at)
        new_last_reaction_at = None if clear_last_reaction_at else (last_reaction_at if last_reaction_at is not None else current.last_reaction_at)
        new_voice_joined_at = None if clear_voice_joined_at else (voice_joined_at if voice_joined_at is not None else current.voice_joined_at)
        new_last_voice_grant_at = None if clear_last_voice_grant_at else (
            last_voice_grant_at if last_voice_grant_at is not None else current.last_voice_grant_at
        )

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "level_runtime")
                await conn.execute(
                    f"""
                    INSERT INTO {table}(guild_id, user_id, last_message_at, last_reaction_at, voice_joined_at, last_voice_grant_at)
                    VALUES($1, $2, $3, $4, $5, $6)
                    ON CONFLICT(guild_id, user_id)
                    DO UPDATE SET
                        last_message_at=EXCLUDED.last_message_at,
                        last_reaction_at=EXCLUDED.last_reaction_at,
                        voice_joined_at=EXCLUDED.voice_joined_at,
                        last_voice_grant_at=EXCLUDED.last_voice_grant_at
                    """,
                    guild_id,
                    user_id,
                    new_last_message_at,
                    new_last_reaction_at,
                    new_voice_joined_at,
                    new_last_voice_grant_at,
                )
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "level_runtime")
            await db.execute(
                f"""
                INSERT INTO level_runtime(guild_id, user_id, last_message_at, last_reaction_at, voice_joined_at, last_voice_grant_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id)
                DO UPDATE SET
                    last_message_at=excluded.last_message_at,
                    last_reaction_at=excluded.last_reaction_at,
                    voice_joined_at=excluded.voice_joined_at,
                    last_voice_grant_at=excluded.last_voice_grant_at
                """.replace("level_runtime", table),
                (guild_id, user_id, new_last_message_at, new_last_reaction_at, new_voice_joined_at, new_last_voice_grant_at),
            )
            await db.commit()

    async def insert_level_event_log(
        self,
        guild_id: int,
        user_id: int,
        event_type: str,
        applied_xp: int,
        total_xp: int,
        level: int,
        reason: str,
        detail_json: dict[str, Any] | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "level_event_logs")
                await conn.execute(
                    f"""
                    INSERT INTO {table}(at, guild_id, user_id, event_type, applied_xp, total_xp, level, reason, detail_json)
                    VALUES($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
                    """,
                    now,
                    guild_id,
                    user_id,
                    event_type,
                    applied_xp,
                    total_xp,
                    level,
                    reason,
                    json.dumps(detail_json) if detail_json is not None else None,
                )
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "level_event_logs")
            await db.execute(
                f"""
                INSERT INTO level_event_logs(at, guild_id, user_id, event_type, applied_xp, total_xp, level, reason, detail_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """.replace("level_event_logs", table),
                (
                    now,
                    guild_id,
                    user_id,
                    event_type,
                    applied_xp,
                    total_xp,
                    level,
                    reason,
                    json.dumps(detail_json) if detail_json is not None else None,
                ),
            )
            await db.commit()

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

    async def get_sticky_runtime(self, guild_id: int, channel_id: int) -> StickyRuntimeRow | None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "sticky_runtime")
                row = await conn.fetchrow(
                    f"""
                    SELECT guild_id, channel_id, message_id, signature, updated_at
                    FROM {table}
                    WHERE guild_id=$1 AND channel_id=$2
                    """,
                    guild_id,
                    channel_id,
                )
            if row is None:
                return None
            return StickyRuntimeRow(
                guild_id=int(row["guild_id"]),
                channel_id=int(row["channel_id"]),
                message_id=int(row["message_id"]),
                signature=str(row["signature"]) if row["signature"] is not None else None,
                updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
            )

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "sticky_runtime")
            cursor = await db.execute(
                f"""
                SELECT guild_id, channel_id, message_id, signature, updated_at
                FROM sticky_runtime
                WHERE guild_id=? AND channel_id=?
                """.replace("sticky_runtime", table),
                (guild_id, channel_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return StickyRuntimeRow(
            guild_id=int(row[0]),
            channel_id=int(row[1]),
            message_id=int(row[2]),
            signature=str(row[3]) if row[3] is not None else None,
            updated_at=str(row[4]),
        )

    async def upsert_sticky_runtime(
        self,
        guild_id: int,
        channel_id: int,
        message_id: int,
        signature: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "sticky_runtime")
                await conn.execute(
                    f"""
                    INSERT INTO {table}(guild_id, channel_id, message_id, signature, updated_at)
                    VALUES($1, $2, $3, $4, $5)
                    ON CONFLICT(guild_id, channel_id)
                    DO UPDATE SET message_id=EXCLUDED.message_id, signature=EXCLUDED.signature, updated_at=EXCLUDED.updated_at
                    """,
                    guild_id,
                    channel_id,
                    int(message_id),
                    signature,
                    now,
                )
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "sticky_runtime")
            await db.execute(
                f"""
                INSERT INTO sticky_runtime(guild_id, channel_id, message_id, signature, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, channel_id)
                DO UPDATE SET message_id=excluded.message_id, signature=excluded.signature, updated_at=excluded.updated_at
                """.replace("sticky_runtime", table),
                (guild_id, channel_id, int(message_id), signature, now),
            )
            await db.commit()

    async def delete_sticky_runtime(self, guild_id: int, channel_id: int) -> None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "sticky_runtime")
                await conn.execute(
                    f"DELETE FROM {table} WHERE guild_id=$1 AND channel_id=$2",
                    guild_id,
                    channel_id,
                )
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "sticky_runtime")
            await db.execute(
                f"DELETE FROM sticky_runtime WHERE guild_id=? AND channel_id=?".replace("sticky_runtime", table),
                (guild_id, channel_id),
            )
            await db.commit()

    async def insert_utility_webhook(
        self,
        ref_id: str,
        guild_id: int,
        channel_id: int,
        webhook_id: int,
        webhook_token: str,
        tag: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                tenant_key = await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn)
                table = self._table_name(tenant_key, "utility_webhooks")
                await conn.execute(
                    f"""
                    INSERT INTO {table}(ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at)
                    VALUES($1, $2, $3, $4, $5, $6, $7)
                    ON CONFLICT(ref_id) DO UPDATE
                    SET guild_id=EXCLUDED.guild_id,
                        channel_id=EXCLUDED.channel_id,
                        webhook_id=EXCLUDED.webhook_id,
                        webhook_token=EXCLUDED.webhook_token,
                        tag=EXCLUDED.tag,
                        created_at=EXCLUDED.created_at
                    """,
                    ref_id,
                    guild_id,
                    channel_id,
                    webhook_id,
                    webhook_token,
                    tag,
                    now,
                )
                await conn.execute(
                    """
                    INSERT INTO utility_webhook_index(ref_id, tenant_key, guild_id, created_at)
                    VALUES($1, $2, $3, $4)
                    ON CONFLICT(ref_id) DO UPDATE SET tenant_key=EXCLUDED.tenant_key, guild_id=EXCLUDED.guild_id
                    """,
                    ref_id,
                    tenant_key,
                    guild_id,
                    now,
                )
            return

        async with aiosqlite.connect(self._sqlite_path) as db:
            tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
            table = self._table_name(tenant_key, "utility_webhooks")
            await db.execute(
                f"""
                INSERT INTO utility_webhooks(ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ref_id) DO UPDATE SET
                    guild_id=excluded.guild_id,
                    channel_id=excluded.channel_id,
                    webhook_id=excluded.webhook_id,
                    webhook_token=excluded.webhook_token,
                    tag=excluded.tag,
                    created_at=excluded.created_at
                """.replace("utility_webhooks", table),
                (ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, now),
            )
            await db.execute(
                """
                INSERT INTO utility_webhook_index(ref_id, tenant_key, guild_id, created_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(ref_id) DO UPDATE SET tenant_key=excluded.tenant_key, guild_id=excluded.guild_id
                """,
                (ref_id, tenant_key, guild_id, now),
            )
            await db.commit()

    async def fetch_utility_webhooks(self, guild_id: int | None = None, limit: int = 200) -> list[UtilityWebhookRow]:
        limit = max(1, min(limit, 1000))
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                if guild_id is None:
                    registry_rows = await conn.fetch(
                        "SELECT tenant_key, scope_id FROM tenant_registry WHERE scope_type='guild' ORDER BY scope_id ASC"
                    )
                    rows: list[Any] = []
                    for registry_row in registry_rows:
                        table = self._table_name(str(registry_row["tenant_key"]), "utility_webhooks")
                        tenant_rows = await conn.fetch(
                            f"""
                            SELECT ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at
                            FROM {table}
                            ORDER BY created_at DESC
                            LIMIT $1
                            """,
                            limit,
                        )
                        rows.extend(tenant_rows)
                    rows.sort(key=lambda row: row["created_at"], reverse=True)
                    rows = rows[:limit]
                else:
                    table = self._table_name(await self._ensure_pg_tenant_tables("guild", guild_id, conn=conn), "utility_webhooks")
                    rows = await conn.fetch(
                        f"""
                        SELECT ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at
                        FROM {table}
                        WHERE guild_id=$1
                        ORDER BY created_at DESC
                        LIMIT $2
                        """,
                        guild_id,
                        limit,
                    )
            return [self._utility_webhook_row(row) for row in rows]

        async with aiosqlite.connect(self._sqlite_path) as db:
            rows: list[tuple[Any, ...]] = []
            if guild_id is None:
                cursor = await db.execute(
                    """
                    SELECT tenant_key, guild_id FROM tenant_registry
                    WHERE scope_type='guild'
                    ORDER BY scope_id ASC
                    """
                )
                guild_tenants = await cursor.fetchall()
            else:
                tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
                guild_tenants = [(tenant_key, guild_id)]
            for tenant_key, tenant_guild_id in guild_tenants:
                table = self._table_name(str(tenant_key), "utility_webhooks")
                cursor = await db.execute(
                    f"""
                    SELECT ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at
                    FROM {table}
                    WHERE guild_id=?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (int(tenant_guild_id), limit),
                )
                rows.extend(await cursor.fetchall())
            rows.sort(key=lambda item: str(item[6]), reverse=True)
            rows = rows[:limit]
        return [self._utility_webhook_row(row) for row in rows]

    async def get_utility_webhook(self, ref_id: str) -> UtilityWebhookRow | None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                index_row = await conn.fetchrow(
                    "SELECT tenant_key FROM utility_webhook_index WHERE ref_id=$1",
                    ref_id,
                )
                if index_row is None:
                    return None
                table = self._table_name(str(index_row["tenant_key"]), "utility_webhooks")
                row = await conn.fetchrow(
                    f"""
                    SELECT ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at
                    FROM {table}
                    WHERE ref_id=$1
                    """,
                    ref_id,
                )
            if row is None:
                return None
            return self._utility_webhook_row(row)

        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                "SELECT tenant_key FROM utility_webhook_index WHERE ref_id=?",
                (ref_id,),
            )
            index_row = await cursor.fetchone()
            if index_row is None:
                return None
            table = self._table_name(str(index_row[0]), "utility_webhooks")
            cursor = await db.execute(
                f"""
                SELECT ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at
                FROM {table}
                WHERE ref_id=?
                """,
                (ref_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._utility_webhook_row(row)

    async def delete_utility_webhook(self, ref_id: str) -> None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                index_row = await conn.fetchrow(
                    "SELECT tenant_key FROM utility_webhook_index WHERE ref_id=$1",
                    ref_id,
                )
                if index_row is not None:
                    table = self._table_name(str(index_row["tenant_key"]), "utility_webhooks")
                    await conn.execute(f"DELETE FROM {table} WHERE ref_id=$1", ref_id)
                await conn.execute("DELETE FROM utility_webhook_index WHERE ref_id=$1", ref_id)
            return
        async with aiosqlite.connect(self._sqlite_path) as db:
            cursor = await db.execute(
                "SELECT tenant_key FROM utility_webhook_index WHERE ref_id=?",
                (ref_id,),
            )
            index_row = await cursor.fetchone()
            if index_row is not None:
                table = self._table_name(str(index_row[0]), "utility_webhooks")
                await db.execute(f"DELETE FROM {table} WHERE ref_id=?", (ref_id,))
            await db.execute("DELETE FROM utility_webhook_index WHERE ref_id=?", (ref_id,))
            await db.commit()

    async def _ensure_sqlite_chat_group_tables(self, db: aiosqlite.Connection) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_groups (
                group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                mode TEXT NOT NULL,
                join_need_apply INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'active',
                leader_guild_id INTEGER NOT NULL,
                rate_limit INTEGER NOT NULL DEFAULT 100,
                overlimit_mode TEXT NOT NULL DEFAULT 'queue',
                slowmode_sec INTEGER NOT NULL DEFAULT 0,
                retention_days INTEGER NOT NULL DEFAULT 30,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_memberships (
                group_id TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                role TEXT NOT NULL DEFAULT 'normal',
                joined_at TEXT NOT NULL,
                PRIMARY KEY(group_id, guild_id)
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_connections (
                group_id TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                webhook_ref TEXT,
                name_format TEXT NOT NULL DEFAULT '{nickname} / {guild_name}',
                PRIMARY KEY(group_id, guild_id)
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_join_applications (
                apply_id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TEXT NOT NULL,
                decided_at TEXT,
                decided_by INTEGER
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_auth_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                guild_id INTEGER,
                key_hash TEXT NOT NULL UNIQUE,
                key_preview TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                source_guild_id INTEGER NOT NULL,
                source_channel_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL,
                author_user_id INTEGER NOT NULL,
                author_name TEXT NOT NULL,
                content TEXT NOT NULL,
                attachment_urls_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_message_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                target_guild_id INTEGER NOT NULL,
                target_channel_id INTEGER NOT NULL,
                target_message_id INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                delivered_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_group_bans (
                group_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(group_id, user_id)
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_guild_bans (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, user_id)
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_global_bans (
                user_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_global_config (
                config_key TEXT PRIMARY KEY,
                config_value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_rate_limit_state (
                group_id TEXT PRIMARY KEY,
                inflight_count INTEGER NOT NULL DEFAULT 0,
                queued_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                actor_user_id INTEGER,
                scope_type TEXT NOT NULL,
                scope_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                detail_json TEXT
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_cli_index (
                guild_id INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                cli_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, group_id),
                UNIQUE(guild_id, cli_id)
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_cli_counters (
                guild_id INTEGER PRIMARY KEY,
                next_cli_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    async def _ensure_pg_chat_group_tables(self, conn: Any) -> None:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_groups (
                group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                mode TEXT NOT NULL,
                join_need_apply BOOLEAN NOT NULL DEFAULT FALSE,
                status TEXT NOT NULL DEFAULT 'active',
                leader_guild_id BIGINT NOT NULL,
                rate_limit INT NOT NULL DEFAULT 100,
                overlimit_mode TEXT NOT NULL DEFAULT 'queue',
                slowmode_sec INT NOT NULL DEFAULT 0,
                retention_days INT NOT NULL DEFAULT 30,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_memberships (
                group_id TEXT NOT NULL,
                guild_id BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                role TEXT NOT NULL DEFAULT 'normal',
                joined_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(group_id, guild_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_connections (
                group_id TEXT NOT NULL,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                webhook_ref TEXT,
                name_format TEXT NOT NULL DEFAULT '{nickname} / {guild_name}',
                PRIMARY KEY(group_id, guild_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_join_applications (
                apply_id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_at TIMESTAMPTZ NOT NULL,
                decided_at TIMESTAMPTZ,
                decided_by BIGINT
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_auth_keys (
                id BIGSERIAL PRIMARY KEY,
                group_id TEXT NOT NULL,
                guild_id BIGINT,
                key_hash TEXT NOT NULL UNIQUE,
                key_preview TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_messages (
                message_id BIGSERIAL PRIMARY KEY,
                group_id TEXT NOT NULL,
                source_guild_id BIGINT NOT NULL,
                source_channel_id BIGINT NOT NULL,
                source_message_id BIGINT NOT NULL,
                author_user_id BIGINT NOT NULL,
                author_name TEXT NOT NULL,
                content TEXT NOT NULL,
                attachment_urls_json JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                deleted BOOLEAN NOT NULL DEFAULT FALSE
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_message_deliveries (
                id BIGSERIAL PRIMARY KEY,
                message_id BIGINT NOT NULL,
                group_id TEXT NOT NULL,
                target_guild_id BIGINT NOT NULL,
                target_channel_id BIGINT NOT NULL,
                target_message_id BIGINT,
                status TEXT NOT NULL,
                error TEXT,
                delivered_at TIMESTAMPTZ NOT NULL
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_group_bans (
                group_id TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(group_id, user_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_guild_bans (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(guild_id, user_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_global_bans (
                user_id BIGINT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_global_config (
                config_key TEXT PRIMARY KEY,
                config_value_json JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_rate_limit_state (
                group_id TEXT PRIMARY KEY,
                inflight_count INT NOT NULL DEFAULT 0,
                queued_count INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_audit_logs (
                id BIGSERIAL PRIMARY KEY,
                at TIMESTAMPTZ NOT NULL,
                actor_user_id BIGINT,
                scope_type TEXT NOT NULL,
                scope_id BIGINT NOT NULL,
                action TEXT NOT NULL,
                detail_json JSONB
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_cli_index (
                guild_id BIGINT NOT NULL,
                group_id TEXT NOT NULL,
                cli_id BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(guild_id, group_id),
                UNIQUE(guild_id, cli_id)
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_group_cli_counters (
                guild_id BIGINT PRIMARY KEY,
                next_cli_id BIGINT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            );
            """
        )

    def _chat_key_hash(self, key_plain: str) -> str:
        return hashlib.sha256(key_plain.encode("utf-8")).hexdigest()

    async def set_chat_group_global_attachment_channel(self, channel_id: int | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        payload = {"attachment_channel_id": int(channel_id)} if channel_id is not None else {"attachment_channel_id": None}
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_global_config(config_key, config_value_json, updated_at)
                    VALUES('attachment-channel-id', $1::jsonb, $2)
                    ON CONFLICT(config_key)
                    DO UPDATE SET config_value_json=EXCLUDED.config_value_json, updated_at=EXCLUDED.updated_at
                    """,
                    json.dumps(payload),
                    now,
                )
            return
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                """
                INSERT INTO chat_group_global_config(config_key, config_value_json, updated_at)
                VALUES('attachment-channel-id', ?, ?)
                ON CONFLICT(config_key)
                DO UPDATE SET config_value_json=excluded.config_value_json, updated_at=excluded.updated_at
                """,
                (json.dumps(payload), now),
            )
            await db.commit()

    async def get_chat_group_global_attachment_channel(self) -> int | None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT config_value_json FROM chat_group_global_config WHERE config_key='attachment-channel-id'"
                )
            if row is None:
                return None
            raw = row["config_value_json"]
            if isinstance(raw, str):
                raw = json.loads(raw)
            if isinstance(raw, dict) and raw.get("attachment_channel_id") is not None:
                return int(raw["attachment_channel_id"])
            return None
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                "SELECT config_value_json FROM chat_group_global_config WHERE config_key='attachment-channel-id'"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        raw = json.loads(str(row[0]))
        if isinstance(raw, dict) and raw.get("attachment_channel_id") is not None:
            return int(raw["attachment_channel_id"])
        return None

    async def create_chat_group(
        self,
        *,
        name: str,
        mode: str,
        leader_guild_id: int,
        channel_id: int,
        webhook_ref: str | None = None,
        join_need_apply: bool | None = None,
    ) -> str:
        group_id = f"cg-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        join_apply = bool(mode == "private") if join_need_apply is None else bool(join_need_apply)
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_groups(
                        group_id, name, mode, join_need_apply, status, leader_guild_id,
                        rate_limit, overlimit_mode, slowmode_sec, retention_days, created_at, updated_at
                    ) VALUES($1,$2,$3,$4,'active',$5,100,'queue',0,30,$6,$6)
                    """,
                    group_id,
                    name,
                    mode,
                    join_apply,
                    int(leader_guild_id),
                    now,
                )
                await conn.execute(
                    """
                    INSERT INTO chat_group_memberships(group_id, guild_id, status, role, joined_at)
                    VALUES($1,$2,'active','leader',$3)
                    ON CONFLICT(group_id,guild_id)
                    DO UPDATE SET status='active', role='leader'
                    """,
                    group_id,
                    int(leader_guild_id),
                    now,
                )
                await conn.execute(
                    """
                    INSERT INTO chat_group_connections(group_id, guild_id, channel_id, webhook_ref, name_format)
                    VALUES($1,$2,$3,$4,'{nickname} / {guild_name}')
                    ON CONFLICT(group_id,guild_id)
                    DO UPDATE SET channel_id=EXCLUDED.channel_id, webhook_ref=EXCLUDED.webhook_ref
                    """,
                    group_id,
                    int(leader_guild_id),
                    int(channel_id),
                    webhook_ref,
                )
            return group_id
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                """
                INSERT INTO chat_group_groups(
                    group_id, name, mode, join_need_apply, status, leader_guild_id,
                    rate_limit, overlimit_mode, slowmode_sec, retention_days, created_at, updated_at
                ) VALUES(?, ?, ?, ?, 'active', ?, 100, 'queue', 0, 30, ?, ?)
                """,
                (group_id, name, mode, 1 if join_apply else 0, int(leader_guild_id), now, now),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO chat_group_memberships(group_id, guild_id, status, role, joined_at)
                VALUES(?, ?, 'active', 'leader', ?)
                """,
                (group_id, int(leader_guild_id), now),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO chat_group_connections(group_id, guild_id, channel_id, webhook_ref, name_format)
                VALUES(?, ?, ?, ?, '{nickname} / {guild_name}')
                """,
                (group_id, int(leader_guild_id), int(channel_id), webhook_ref),
            )
            await db.commit()
        return group_id

    async def get_chat_group(self, group_id: str) -> ChatGroupRow | None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM chat_group_groups WHERE group_id=$1", group_id)
            if row is None:
                return None
            return ChatGroupRow(
                group_id=str(row["group_id"]),
                name=str(row["name"]),
                mode=str(row["mode"]),
                join_need_apply=bool(row["join_need_apply"]),
                status=str(row["status"]),
                leader_guild_id=int(row["leader_guild_id"]),
                rate_limit=int(row["rate_limit"]),
                overlimit_mode=str(row["overlimit_mode"]),
                slowmode_sec=int(row["slowmode_sec"]),
                retention_days=int(row["retention_days"]),
                created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
                updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
            )
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT group_id, name, mode, join_need_apply, status, leader_guild_id, rate_limit, overlimit_mode,
                       slowmode_sec, retention_days, created_at, updated_at
                FROM chat_group_groups WHERE group_id=?
                """,
                (group_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ChatGroupRow(
            group_id=str(row[0]),
            name=str(row[1]),
            mode=str(row[2]),
            join_need_apply=bool(row[3]),
            status=str(row[4]),
            leader_guild_id=int(row[5]),
            rate_limit=int(row[6]),
            overlimit_mode=str(row[7]),
            slowmode_sec=int(row[8]),
            retention_days=int(row[9]),
            created_at=str(row[10]),
            updated_at=str(row[11]),
        )

    async def list_chat_groups_for_guild(self, guild_id: int) -> list[ChatGroupRow]:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT g.*
                    FROM chat_group_groups g
                    JOIN chat_group_memberships m ON g.group_id=m.group_id
                    WHERE m.guild_id=$1
                    ORDER BY g.created_at DESC
                    """,
                    int(guild_id),
                )
            return [
                ChatGroupRow(
                    group_id=str(row["group_id"]),
                    name=str(row["name"]),
                    mode=str(row["mode"]),
                    join_need_apply=bool(row["join_need_apply"]),
                    status=str(row["status"]),
                    leader_guild_id=int(row["leader_guild_id"]),
                    rate_limit=int(row["rate_limit"]),
                    overlimit_mode=str(row["overlimit_mode"]),
                    slowmode_sec=int(row["slowmode_sec"]),
                    retention_days=int(row["retention_days"]),
                    created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
                    updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
                )
                for row in rows
            ]
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT g.group_id, g.name, g.mode, g.join_need_apply, g.status, g.leader_guild_id, g.rate_limit,
                       g.overlimit_mode, g.slowmode_sec, g.retention_days, g.created_at, g.updated_at
                FROM chat_group_groups g
                JOIN chat_group_memberships m ON g.group_id=m.group_id
                WHERE m.guild_id=?
                ORDER BY g.created_at DESC
                """,
                (int(guild_id),),
            )
            rows = await cursor.fetchall()
        return [
            ChatGroupRow(
                group_id=str(row[0]),
                name=str(row[1]),
                mode=str(row[2]),
                join_need_apply=bool(row[3]),
                status=str(row[4]),
                leader_guild_id=int(row[5]),
                rate_limit=int(row[6]),
                overlimit_mode=str(row[7]),
                slowmode_sec=int(row[8]),
                retention_days=int(row[9]),
                created_at=str(row[10]),
                updated_at=str(row[11]),
            )
            for row in rows
        ]

    async def list_chat_group_memberships(self, group_id: str) -> list[ChatGroupMembershipRow]:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT group_id, guild_id, status, role, joined_at
                    FROM chat_group_memberships
                    WHERE group_id=$1
                    ORDER BY guild_id ASC
                    """,
                    group_id,
                )
            return [
                ChatGroupMembershipRow(
                    group_id=str(row["group_id"]),
                    guild_id=int(row["guild_id"]),
                    status=str(row["status"]),
                    role=str(row["role"]),
                    joined_at=row["joined_at"].isoformat() if hasattr(row["joined_at"], "isoformat") else str(row["joined_at"]),
                )
                for row in rows
            ]
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT group_id, guild_id, status, role, joined_at
                FROM chat_group_memberships
                WHERE group_id=?
                ORDER BY guild_id ASC
                """,
                (group_id,),
            )
            rows = await cursor.fetchall()
        return [
            ChatGroupMembershipRow(
                group_id=str(row[0]),
                guild_id=int(row[1]),
                status=str(row[2]),
                role=str(row[3]),
                joined_at=str(row[4]),
            )
            for row in rows
        ]

    async def get_chat_group_membership(self, group_id: str, guild_id: int) -> ChatGroupMembershipRow | None:
        rows = await self.list_chat_group_memberships(group_id)
        for row in rows:
            if row.guild_id == int(guild_id):
                return row
        return None

    async def upsert_chat_group_membership(
        self,
        *,
        group_id: str,
        guild_id: int,
        status: str,
        role: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_memberships(group_id, guild_id, status, role, joined_at)
                    VALUES($1,$2,$3,$4,$5)
                    ON CONFLICT(group_id,guild_id)
                    DO UPDATE SET status=EXCLUDED.status, role=EXCLUDED.role
                    """,
                    group_id,
                    int(guild_id),
                    status,
                    role,
                    now,
                )
            return
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                """
                INSERT OR REPLACE INTO chat_group_memberships(group_id, guild_id, status, role, joined_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (group_id, int(guild_id), status, role, now),
            )
            await db.commit()

    async def upsert_chat_group_connection(
        self,
        *,
        group_id: str,
        guild_id: int,
        channel_id: int,
        webhook_ref: str | None,
        name_format: str = "{nickname} / {guild_name}",
    ) -> None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_connections(group_id, guild_id, channel_id, webhook_ref, name_format)
                    VALUES($1,$2,$3,$4,$5)
                    ON CONFLICT(group_id,guild_id)
                    DO UPDATE SET channel_id=EXCLUDED.channel_id, webhook_ref=EXCLUDED.webhook_ref, name_format=EXCLUDED.name_format
                    """,
                    group_id,
                    int(guild_id),
                    int(channel_id),
                    webhook_ref,
                    name_format,
                )
            return
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                """
                INSERT OR REPLACE INTO chat_group_connections(group_id, guild_id, channel_id, webhook_ref, name_format)
                VALUES(?, ?, ?, ?, ?)
                """,
                (group_id, int(guild_id), int(channel_id), webhook_ref, name_format),
            )
            await db.commit()

    async def list_chat_group_connections(self, group_id: str) -> list[ChatGroupConnectionRow]:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT group_id, guild_id, channel_id, webhook_ref, name_format
                    FROM chat_group_connections
                    WHERE group_id=$1
                    ORDER BY guild_id ASC
                    """,
                    group_id,
                )
            return [
                ChatGroupConnectionRow(
                    group_id=str(row["group_id"]),
                    guild_id=int(row["guild_id"]),
                    channel_id=int(row["channel_id"]),
                    webhook_ref=str(row["webhook_ref"]) if row["webhook_ref"] else None,
                    name_format=str(row["name_format"]),
                )
                for row in rows
            ]
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT group_id, guild_id, channel_id, webhook_ref, name_format
                FROM chat_group_connections
                WHERE group_id=?
                ORDER BY guild_id ASC
                """,
                (group_id,),
            )
            rows = await cursor.fetchall()
        return [
            ChatGroupConnectionRow(
                group_id=str(row[0]),
                guild_id=int(row[1]),
                channel_id=int(row[2]),
                webhook_ref=str(row[3]) if row[3] is not None else None,
                name_format=str(row[4]),
            )
            for row in rows
        ]

    async def create_chat_group_application(self, group_id: str, guild_id: int, channel_id: int) -> str:
        now = datetime.now(timezone.utc).isoformat()
        apply_id = f"cga-{uuid.uuid4().hex[:10]}"
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_join_applications(apply_id, group_id, guild_id, channel_id, status, requested_at)
                    VALUES($1,$2,$3,$4,'pending',$5)
                    """,
                    apply_id,
                    group_id,
                    int(guild_id),
                    int(channel_id),
                    now,
                )
            return apply_id
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                """
                INSERT INTO chat_group_join_applications(apply_id, group_id, guild_id, channel_id, status, requested_at)
                VALUES(?, ?, ?, ?, 'pending', ?)
                """,
                (apply_id, group_id, int(guild_id), int(channel_id), now),
            )
            await db.commit()
        return apply_id

    async def list_chat_group_applications(self, group_id: str, *, status: str | None = None) -> list[ChatGroupApplicationRow]:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                if status is None:
                    rows = await conn.fetch(
                        """
                        SELECT apply_id, group_id, guild_id, channel_id, status, requested_at, decided_at, decided_by
                        FROM chat_group_join_applications
                        WHERE group_id=$1
                        ORDER BY requested_at ASC
                        """,
                        group_id,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT apply_id, group_id, guild_id, channel_id, status, requested_at, decided_at, decided_by
                        FROM chat_group_join_applications
                        WHERE group_id=$1 AND status=$2
                        ORDER BY requested_at ASC
                        """,
                        group_id,
                        status,
                    )
            return [
                ChatGroupApplicationRow(
                    apply_id=str(row["apply_id"]),
                    group_id=str(row["group_id"]),
                    guild_id=int(row["guild_id"]),
                    channel_id=int(row["channel_id"]),
                    status=str(row["status"]),
                    requested_at=row["requested_at"].isoformat() if hasattr(row["requested_at"], "isoformat") else str(row["requested_at"]),
                    decided_at=row["decided_at"].isoformat() if row["decided_at"] and hasattr(row["decided_at"], "isoformat") else (str(row["decided_at"]) if row["decided_at"] else None),
                    decided_by=int(row["decided_by"]) if row["decided_by"] is not None else None,
                )
                for row in rows
            ]
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            if status is None:
                cursor = await db.execute(
                    """
                    SELECT apply_id, group_id, guild_id, channel_id, status, requested_at, decided_at, decided_by
                    FROM chat_group_join_applications
                    WHERE group_id=?
                    ORDER BY requested_at ASC
                    """,
                    (group_id,),
                )
            else:
                cursor = await db.execute(
                    """
                    SELECT apply_id, group_id, guild_id, channel_id, status, requested_at, decided_at, decided_by
                    FROM chat_group_join_applications
                    WHERE group_id=? AND status=?
                    ORDER BY requested_at ASC
                    """,
                    (group_id, status),
                )
            rows = await cursor.fetchall()
        return [
            ChatGroupApplicationRow(
                apply_id=str(row[0]),
                group_id=str(row[1]),
                guild_id=int(row[2]),
                channel_id=int(row[3]),
                status=str(row[4]),
                requested_at=str(row[5]),
                decided_at=str(row[6]) if row[6] is not None else None,
                decided_by=int(row[7]) if row[7] is not None else None,
            )
            for row in rows
        ]

    async def decide_chat_group_application(self, apply_id: str, status: str, decided_by: int | None = None) -> ChatGroupApplicationRow | None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE chat_group_join_applications
                    SET status=$2, decided_at=$3, decided_by=$4
                    WHERE apply_id=$1 AND status='pending'
                    RETURNING apply_id, group_id, guild_id, channel_id, status, requested_at, decided_at, decided_by
                    """,
                    apply_id,
                    status,
                    now,
                    decided_by,
                )
            if row is None:
                return None
            return ChatGroupApplicationRow(
                apply_id=str(row["apply_id"]),
                group_id=str(row["group_id"]),
                guild_id=int(row["guild_id"]),
                channel_id=int(row["channel_id"]),
                status=str(row["status"]),
                requested_at=row["requested_at"].isoformat() if hasattr(row["requested_at"], "isoformat") else str(row["requested_at"]),
                decided_at=row["decided_at"].isoformat() if row["decided_at"] and hasattr(row["decided_at"], "isoformat") else (str(row["decided_at"]) if row["decided_at"] else None),
                decided_by=int(row["decided_by"]) if row["decided_by"] is not None else None,
            )
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT apply_id
                FROM chat_group_join_applications
                WHERE apply_id=? AND status='pending'
                """,
                (apply_id,),
            )
            pending_row = await cursor.fetchone()
            if pending_row is None:
                return None
            await db.execute(
                """
                UPDATE chat_group_join_applications
                SET status=?, decided_at=?, decided_by=?
                WHERE apply_id=? AND status='pending'
                """,
                (status, now, decided_by, apply_id),
            )
            cursor = await db.execute(
                """
                SELECT apply_id, group_id, guild_id, channel_id, status, requested_at, decided_at, decided_by
                FROM chat_group_join_applications
                WHERE apply_id=?
                """,
                (apply_id,),
            )
            row = await cursor.fetchone()
            await db.commit()
        if row is None:
            return None
        return ChatGroupApplicationRow(
            apply_id=str(row[0]),
            group_id=str(row[1]),
            guild_id=int(row[2]),
            channel_id=int(row[3]),
            status=str(row[4]),
            requested_at=str(row[5]),
            decided_at=str(row[6]) if row[6] is not None else None,
            decided_by=int(row[7]) if row[7] is not None else None,
        )

    async def create_chat_group_auth_key(self, group_id: str, *, guild_id: int | None = None) -> tuple[int, str]:
        plain = f"cgk-{uuid.uuid4().hex[:16]}"
        key_hash = self._chat_key_hash(plain)
        preview = plain[:8] + "..."
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO chat_group_auth_keys(group_id, guild_id, key_hash, key_preview, status, created_at)
                    VALUES($1,$2,$3,$4,'active',$5)
                    RETURNING id
                    """,
                    group_id,
                    guild_id,
                    key_hash,
                    preview,
                    now,
                )
            return int(row["id"]), plain
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                INSERT INTO chat_group_auth_keys(group_id, guild_id, key_hash, key_preview, status, created_at)
                VALUES(?, ?, ?, ?, 'active', ?)
                """,
                (group_id, guild_id, key_hash, preview, now),
            )
            await db.commit()
            key_id = int(cursor.lastrowid)
        return key_id, plain

    async def list_chat_group_auth_keys(self, group_id: str) -> list[ChatGroupAuthKeyRow]:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, group_id, guild_id, key_preview, status, created_at, revoked_at
                    FROM chat_group_auth_keys
                    WHERE group_id=$1
                    ORDER BY id ASC
                    """,
                    group_id,
                )
            return [
                ChatGroupAuthKeyRow(
                    id=int(row["id"]),
                    group_id=str(row["group_id"]),
                    guild_id=int(row["guild_id"]) if row["guild_id"] is not None else None,
                    key_preview=str(row["key_preview"]),
                    status=str(row["status"]),
                    created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
                    revoked_at=row["revoked_at"].isoformat() if row["revoked_at"] and hasattr(row["revoked_at"], "isoformat") else (str(row["revoked_at"]) if row["revoked_at"] else None),
                )
                for row in rows
            ]
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT id, group_id, guild_id, key_preview, status, created_at, revoked_at
                FROM chat_group_auth_keys
                WHERE group_id=?
                ORDER BY id ASC
                """,
                (group_id,),
            )
            rows = await cursor.fetchall()
        return [
            ChatGroupAuthKeyRow(
                id=int(row[0]),
                group_id=str(row[1]),
                guild_id=int(row[2]) if row[2] is not None else None,
                key_preview=str(row[3]),
                status=str(row[4]),
                created_at=str(row[5]),
                revoked_at=str(row[6]) if row[6] is not None else None,
            )
            for row in rows
        ]

    async def revoke_chat_group_auth_key(self, group_id: str, key_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE chat_group_auth_keys
                    SET status='revoked', revoked_at=$3
                    WHERE group_id=$1 AND id=$2
                    """,
                    group_id,
                    int(key_id),
                    now,
                )
            return result.endswith("1")
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                UPDATE chat_group_auth_keys
                SET status='revoked', revoked_at=?
                WHERE group_id=? AND id=?
                """,
                (now, group_id, int(key_id)),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def resolve_chat_group_auth_key(self, group_id: str, key_plain: str, guild_id: int) -> bool:
        key_hash = self._chat_key_hash(key_plain)
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT id
                    FROM chat_group_auth_keys
                    WHERE group_id=$1
                      AND key_hash=$2
                      AND status='active'
                      AND (guild_id IS NULL OR guild_id=$3)
                    """,
                    group_id,
                    key_hash,
                    int(guild_id),
                )
            return row is not None
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT id
                FROM chat_group_auth_keys
                WHERE group_id=?
                  AND key_hash=?
                  AND status='active'
                  AND (guild_id IS NULL OR guild_id=?)
                """,
                (group_id, key_hash, int(guild_id)),
            )
            row = await cursor.fetchone()
        return row is not None

    async def set_chat_group_role(self, group_id: str, guild_id: int, role: str) -> None:
        current = await self.get_chat_group_membership(group_id, guild_id)
        status = current.status if current else "active"
        await self.upsert_chat_group_membership(group_id=group_id, guild_id=guild_id, status=status, role=role)

    async def transfer_chat_group_leader(self, group_id: str, guild_id: int) -> None:
        memberships = await self.list_chat_group_memberships(group_id)
        for row in memberships:
            role = "leader" if row.guild_id == int(guild_id) else ("manager" if row.role == "leader" else row.role)
            await self.upsert_chat_group_membership(group_id=group_id, guild_id=row.guild_id, status=row.status, role=role)
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute("UPDATE chat_group_groups SET leader_guild_id=$2, updated_at=$3 WHERE group_id=$1", group_id, int(guild_id), datetime.now(timezone.utc).isoformat())
            return
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                "UPDATE chat_group_groups SET leader_guild_id=?, updated_at=? WHERE group_id=?",
                (int(guild_id), datetime.now(timezone.utc).isoformat(), group_id),
            )
            await db.commit()

    async def set_chat_group_ban(self, *, group_id: str | None, guild_id: int | None, user_id: int, mode: Literal["ban", "unban"], global_scope: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat()
        table = "chat_group_global_bans" if global_scope else ("chat_group_group_bans" if group_id is not None else "chat_group_guild_bans")
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                if mode == "ban":
                    if global_scope:
                        await conn.execute(
                            f"INSERT INTO {table}(user_id, created_at) VALUES($1,$2) ON CONFLICT(user_id) DO NOTHING",
                            int(user_id),
                            now,
                        )
                    elif group_id is not None:
                        await conn.execute(
                            f"INSERT INTO {table}(group_id, user_id, created_at) VALUES($1,$2,$3) ON CONFLICT(group_id,user_id) DO NOTHING",
                            group_id,
                            int(user_id),
                            now,
                        )
                    else:
                        await conn.execute(
                            f"INSERT INTO {table}(guild_id, user_id, created_at) VALUES($1,$2,$3) ON CONFLICT(guild_id,user_id) DO NOTHING",
                            int(guild_id or 0),
                            int(user_id),
                            now,
                        )
                else:
                    if global_scope:
                        await conn.execute(f"DELETE FROM {table} WHERE user_id=$1", int(user_id))
                    elif group_id is not None:
                        await conn.execute(f"DELETE FROM {table} WHERE group_id=$1 AND user_id=$2", group_id, int(user_id))
                    else:
                        await conn.execute(f"DELETE FROM {table} WHERE guild_id=$1 AND user_id=$2", int(guild_id or 0), int(user_id))
            return
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            if mode == "ban":
                if global_scope:
                    await db.execute(
                        f"INSERT OR IGNORE INTO {table}(user_id, created_at) VALUES(?, ?)",
                        (int(user_id), now),
                    )
                elif group_id is not None:
                    await db.execute(
                        f"INSERT OR IGNORE INTO {table}(group_id, user_id, created_at) VALUES(?, ?, ?)",
                        (group_id, int(user_id), now),
                    )
                else:
                    await db.execute(
                        f"INSERT OR IGNORE INTO {table}(guild_id, user_id, created_at) VALUES(?, ?, ?)",
                        (int(guild_id or 0), int(user_id), now),
                    )
            else:
                if global_scope:
                    await db.execute(f"DELETE FROM {table} WHERE user_id=?", (int(user_id),))
                elif group_id is not None:
                    await db.execute(f"DELETE FROM {table} WHERE group_id=? AND user_id=?", (group_id, int(user_id)))
                else:
                    await db.execute(f"DELETE FROM {table} WHERE guild_id=? AND user_id=?", (int(guild_id or 0), int(user_id)))
            await db.commit()

    async def is_chat_group_user_banned(self, group_id: str, guild_id: int, user_id: int) -> bool:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                global_row = await conn.fetchrow("SELECT user_id FROM chat_group_global_bans WHERE user_id=$1", int(user_id))
                if global_row is not None:
                    return True
                guild_row = await conn.fetchrow(
                    "SELECT user_id FROM chat_group_guild_bans WHERE guild_id=$1 AND user_id=$2",
                    int(guild_id),
                    int(user_id),
                )
                if guild_row is not None:
                    return True
                group_row = await conn.fetchrow(
                    "SELECT user_id FROM chat_group_group_bans WHERE group_id=$1 AND user_id=$2",
                    group_id,
                    int(user_id),
                )
                return group_row is not None
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            for query, params in (
                ("SELECT user_id FROM chat_group_global_bans WHERE user_id=?", (int(user_id),)),
                ("SELECT user_id FROM chat_group_guild_bans WHERE guild_id=? AND user_id=?", (int(guild_id), int(user_id))),
                ("SELECT user_id FROM chat_group_group_bans WHERE group_id=? AND user_id=?", (group_id, int(user_id))),
            ):
                cursor = await db.execute(query, params)
                if await cursor.fetchone() is not None:
                    return True
        return False

    async def get_chat_group_rate_limit_state(self, group_id: str) -> ChatGroupRateLimitStateRow:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_rate_limit_state(group_id, inflight_count, queued_count, updated_at)
                    VALUES($1, 0, 0, $2)
                    ON CONFLICT(group_id) DO NOTHING
                    """,
                    group_id,
                    now,
                )
                row = await conn.fetchrow(
                    """
                    SELECT group_id, inflight_count, queued_count, updated_at
                    FROM chat_group_rate_limit_state
                    WHERE group_id=$1
                    """,
                    group_id,
                )
            if row is None:
                return ChatGroupRateLimitStateRow(group_id=group_id, inflight_count=0, queued_count=0, updated_at=now)
            return ChatGroupRateLimitStateRow(
                group_id=str(row["group_id"]),
                inflight_count=max(0, int(row["inflight_count"])),
                queued_count=max(0, int(row["queued_count"])),
                updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
            )

        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                """
                INSERT OR IGNORE INTO chat_group_rate_limit_state(group_id, inflight_count, queued_count, updated_at)
                VALUES(?, 0, 0, ?)
                """,
                (group_id, now),
            )
            cursor = await db.execute(
                """
                SELECT group_id, inflight_count, queued_count, updated_at
                FROM chat_group_rate_limit_state
                WHERE group_id=?
                """,
                (group_id,),
            )
            row = await cursor.fetchone()
            await db.commit()
        if row is None:
            return ChatGroupRateLimitStateRow(group_id=group_id, inflight_count=0, queued_count=0, updated_at=now)
        return ChatGroupRateLimitStateRow(
            group_id=str(row[0]),
            inflight_count=max(0, int(row[1])),
            queued_count=max(0, int(row[2])),
            updated_at=str(row[3]),
        )

    async def increment_chat_group_queue(self, group_id: str, *, queued_delta: int = 0, inflight_delta: int = 0) -> ChatGroupRateLimitStateRow:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_rate_limit_state(group_id, inflight_count, queued_count, updated_at)
                    VALUES($1, 0, 0, $2)
                    ON CONFLICT(group_id) DO NOTHING
                    """,
                    group_id,
                    now,
                )
                row = await conn.fetchrow(
                    """
                    UPDATE chat_group_rate_limit_state
                    SET queued_count=GREATEST(0, queued_count + $2),
                        inflight_count=GREATEST(0, inflight_count + $3),
                        updated_at=$4
                    WHERE group_id=$1
                    RETURNING group_id, inflight_count, queued_count, updated_at
                    """,
                    group_id,
                    int(queued_delta),
                    int(inflight_delta),
                    now,
                )
            if row is None:
                return ChatGroupRateLimitStateRow(group_id=group_id, inflight_count=0, queued_count=0, updated_at=now)
            return ChatGroupRateLimitStateRow(
                group_id=str(row["group_id"]),
                inflight_count=max(0, int(row["inflight_count"])),
                queued_count=max(0, int(row["queued_count"])),
                updated_at=row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
            )

        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                """
                INSERT OR IGNORE INTO chat_group_rate_limit_state(group_id, inflight_count, queued_count, updated_at)
                VALUES(?, 0, 0, ?)
                """,
                (group_id, now),
            )
            await db.execute(
                """
                UPDATE chat_group_rate_limit_state
                SET queued_count=MAX(0, queued_count + ?),
                    inflight_count=MAX(0, inflight_count + ?),
                    updated_at=?
                WHERE group_id=?
                """,
                (int(queued_delta), int(inflight_delta), now, group_id),
            )
            cursor = await db.execute(
                """
                SELECT group_id, inflight_count, queued_count, updated_at
                FROM chat_group_rate_limit_state
                WHERE group_id=?
                """,
                (group_id,),
            )
            row = await cursor.fetchone()
            await db.commit()
        if row is None:
            return ChatGroupRateLimitStateRow(group_id=group_id, inflight_count=0, queued_count=0, updated_at=now)
        return ChatGroupRateLimitStateRow(
            group_id=str(row[0]),
            inflight_count=max(0, int(row[1])),
            queued_count=max(0, int(row[2])),
            updated_at=str(row[3]),
        )

    async def reset_chat_group_rate_limit_states(self, group_id: str | None = None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                if group_id is None:
                    changed = await conn.fetchval(
                        """
                        WITH updated AS (
                            UPDATE chat_group_rate_limit_state
                            SET queued_count=0, inflight_count=0, updated_at=$1
                            RETURNING 1
                        )
                        SELECT COUNT(*) FROM updated
                        """,
                        now,
                    )
                else:
                    changed = await conn.fetchval(
                        """
                        WITH updated AS (
                            UPDATE chat_group_rate_limit_state
                            SET queued_count=0, inflight_count=0, updated_at=$2
                            WHERE group_id=$1
                            RETURNING 1
                        )
                        SELECT COUNT(*) FROM updated
                        """,
                        group_id,
                        now,
                    )
            return int(changed or 0)

        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            if group_id is None:
                cursor = await db.execute(
                    """
                    UPDATE chat_group_rate_limit_state
                    SET queued_count=0, inflight_count=0, updated_at=?
                    """,
                    (now,),
                )
            else:
                cursor = await db.execute(
                    """
                    UPDATE chat_group_rate_limit_state
                    SET queued_count=0, inflight_count=0, updated_at=?
                    WHERE group_id=?
                    """,
                    (now, group_id),
                )
            await db.commit()
            return int(cursor.rowcount or 0)

    async def insert_chat_group_message(
        self,
        *,
        group_id: str,
        source_guild_id: int,
        source_channel_id: int,
        source_message_id: int,
        author_user_id: int,
        author_name: str,
        content: str,
        attachment_urls: list[str],
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        payload_json = json.dumps(list(attachment_urls))
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    INSERT INTO chat_group_messages(
                        group_id, source_guild_id, source_channel_id, source_message_id, author_user_id, author_name,
                        content, attachment_urls_json, created_at, deleted
                    ) VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,FALSE)
                    RETURNING message_id
                    """,
                    group_id,
                    int(source_guild_id),
                    int(source_channel_id),
                    int(source_message_id),
                    int(author_user_id),
                    author_name,
                    content,
                    payload_json,
                    now,
                )
            return int(row["message_id"])
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                INSERT INTO chat_group_messages(
                    group_id, source_guild_id, source_channel_id, source_message_id, author_user_id, author_name,
                    content, attachment_urls_json, created_at, deleted
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    group_id,
                    int(source_guild_id),
                    int(source_channel_id),
                    int(source_message_id),
                    int(author_user_id),
                    author_name,
                    content,
                    payload_json,
                    now,
                ),
            )
            await db.commit()
            return int(cursor.lastrowid)

    async def get_chat_group_message(self, message_id: int) -> ChatGroupMessageRow | None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT message_id, group_id, source_guild_id, source_channel_id, source_message_id,
                           author_user_id, author_name, content, attachment_urls_json, created_at, deleted
                    FROM chat_group_messages
                    WHERE message_id=$1
                    """,
                    int(message_id),
                )
            if row is None:
                return None
            raw_attachments = row["attachment_urls_json"]
            if isinstance(raw_attachments, str):
                raw_attachments = json.loads(raw_attachments)
            return ChatGroupMessageRow(
                message_id=int(row["message_id"]),
                group_id=str(row["group_id"]),
                source_guild_id=int(row["source_guild_id"]),
                source_channel_id=int(row["source_channel_id"]),
                source_message_id=int(row["source_message_id"]),
                author_user_id=int(row["author_user_id"]),
                author_name=str(row["author_name"]),
                content=str(row["content"]),
                attachment_urls=list(raw_attachments) if isinstance(raw_attachments, list) else [],
                created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
                deleted=bool(row["deleted"]),
            )
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT message_id, group_id, source_guild_id, source_channel_id, source_message_id,
                       author_user_id, author_name, content, attachment_urls_json, created_at, deleted
                FROM chat_group_messages
                WHERE message_id=?
                """,
                (int(message_id),),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        raw = json.loads(str(row[8])) if row[8] else []
        return ChatGroupMessageRow(
            message_id=int(row[0]),
            group_id=str(row[1]),
            source_guild_id=int(row[2]),
            source_channel_id=int(row[3]),
            source_message_id=int(row[4]),
            author_user_id=int(row[5]),
            author_name=str(row[6]),
            content=str(row[7]),
            attachment_urls=list(raw) if isinstance(raw, list) else [],
            created_at=str(row[9]),
            deleted=bool(row[10]),
        )

    async def mark_chat_group_message_deleted(self, message_id: int) -> None:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute("UPDATE chat_group_messages SET deleted=TRUE WHERE message_id=$1", int(message_id))
            return
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute("UPDATE chat_group_messages SET deleted=1 WHERE message_id=?", (int(message_id),))
            await db.commit()

    async def insert_chat_group_delivery(
        self,
        *,
        message_id: int,
        group_id: str,
        target_guild_id: int,
        target_channel_id: int,
        target_message_id: int | None,
        status: str,
        error: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_message_deliveries(
                        message_id, group_id, target_guild_id, target_channel_id, target_message_id, status, error, delivered_at
                    ) VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                    """,
                    int(message_id),
                    group_id,
                    int(target_guild_id),
                    int(target_channel_id),
                    int(target_message_id) if target_message_id is not None else None,
                    status,
                    error,
                    now,
                )
            return
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.execute(
                """
                INSERT INTO chat_group_message_deliveries(
                    message_id, group_id, target_guild_id, target_channel_id, target_message_id, status, error, delivered_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(message_id),
                    group_id,
                    int(target_guild_id),
                    int(target_channel_id),
                    int(target_message_id) if target_message_id is not None else None,
                    status,
                    error,
                    now,
                ),
            )
            await db.commit()

    async def list_chat_group_deliveries(self, message_id: int) -> list[dict[str, Any]]:
        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT target_guild_id, target_channel_id, target_message_id, status, error, delivered_at
                    FROM chat_group_message_deliveries
                    WHERE message_id=$1
                    ORDER BY id ASC
                    """,
                    int(message_id),
                )
            return [
                {
                    "target_guild_id": int(row["target_guild_id"]),
                    "target_channel_id": int(row["target_channel_id"]),
                    "target_message_id": int(row["target_message_id"]) if row["target_message_id"] is not None else None,
                    "status": str(row["status"]),
                    "error": str(row["error"]) if row["error"] is not None else None,
                    "delivered_at": row["delivered_at"].isoformat() if hasattr(row["delivered_at"], "isoformat") else str(row["delivered_at"]),
                }
                for row in rows
            ]
        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            cursor = await db.execute(
                """
                SELECT target_guild_id, target_channel_id, target_message_id, status, error, delivered_at
                FROM chat_group_message_deliveries
                WHERE message_id=?
                ORDER BY id ASC
                """,
                (int(message_id),),
            )
            rows = await cursor.fetchall()
        return [
            {
                "target_guild_id": int(row[0]),
                "target_channel_id": int(row[1]),
                "target_message_id": int(row[2]) if row[2] is not None else None,
                "status": str(row[3]),
                "error": str(row[4]) if row[4] is not None else None,
                "delivered_at": str(row[5]),
            }
            for row in rows
        ]

    async def resolve_chat_group_cli_index(self, guild_id: int, group_ids: list[str]) -> dict[str, int]:
        unique_group_ids = sorted({str(group_id) for group_id in group_ids if str(group_id)})
        if not unique_group_ids:
            return {}
        now = datetime.now(timezone.utc).isoformat()

        if self.backend == "postgres":
            await self._ensure_pg_pool()
            async with self._pg_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_group_cli_counters(guild_id, next_cli_id, updated_at)
                    VALUES(
                        $1,
                        COALESCE((SELECT MAX(cli_id) + 1 FROM chat_group_cli_index WHERE guild_id=$1), 1),
                        $2
                    )
                    ON CONFLICT(guild_id) DO NOTHING
                    """,
                    int(guild_id),
                    now,
                )
                for group_id in unique_group_ids:
                    existing_cli = await conn.fetchval(
                        """
                        SELECT cli_id
                        FROM chat_group_cli_index
                        WHERE guild_id=$1 AND group_id=$2
                        """,
                        int(guild_id),
                        group_id,
                    )
                    if existing_cli is not None:
                        continue
                    for _ in range(5):
                        row = await conn.fetchrow(
                            """
                            UPDATE chat_group_cli_counters
                            SET next_cli_id=next_cli_id + 1, updated_at=$2
                            WHERE guild_id=$1
                            RETURNING next_cli_id - 1 AS cli_id
                            """,
                            int(guild_id),
                            now,
                        )
                        if row is None:
                            break
                        next_cli = int(row["cli_id"])
                        await conn.execute(
                            """
                            INSERT INTO chat_group_cli_index(guild_id, group_id, cli_id, created_at)
                            VALUES($1, $2, $3, $4)
                            ON CONFLICT DO NOTHING
                            """,
                            int(guild_id),
                            group_id,
                            next_cli,
                            now,
                        )
                        existing_cli = await conn.fetchval(
                            """
                            SELECT cli_id
                            FROM chat_group_cli_index
                            WHERE guild_id=$1 AND group_id=$2
                            """,
                            int(guild_id),
                            group_id,
                        )
                        if existing_cli is not None:
                            break
                    if existing_cli is None:
                        raise RuntimeError(f"failed to allocate chat-group cli_id for guild={guild_id} group={group_id}")
                final_rows = await conn.fetch(
                    """
                    SELECT group_id, cli_id
                    FROM chat_group_cli_index
                    WHERE guild_id=$1 AND group_id = ANY($2::text[])
                    """,
                    int(guild_id),
                    unique_group_ids,
                )
            return {str(row["group_id"]): int(row["cli_id"]) for row in final_rows}

        async with aiosqlite.connect(self._sqlite_path) as db:
            await self._ensure_sqlite_chat_group_tables(db)
            await db.commit()
            await db.execute("BEGIN IMMEDIATE")
            try:
                placeholders = ",".join("?" for _ in unique_group_ids)
                cursor = await db.execute(
                    f"""
                    SELECT group_id, cli_id
                    FROM chat_group_cli_index
                    WHERE guild_id=? AND group_id IN ({placeholders})
                    """,
                    (int(guild_id), *unique_group_ids),
                )
                existing_rows = await cursor.fetchall()
                mapping = {str(row[0]): int(row[1]) for row in existing_rows}

                counter_row = await (await db.execute(
                    "SELECT next_cli_id FROM chat_group_cli_counters WHERE guild_id=?",
                    (int(guild_id),),
                )).fetchone()
                if counter_row is None:
                    max_row = await (await db.execute(
                        "SELECT COALESCE(MAX(cli_id) + 1, 1) FROM chat_group_cli_index WHERE guild_id=?",
                        (int(guild_id),),
                    )).fetchone()
                    initial_next = int(max_row[0] if max_row is not None else 1)
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO chat_group_cli_counters(guild_id, next_cli_id, updated_at)
                        VALUES(?, ?, ?)
                        """,
                        (int(guild_id), initial_next, now),
                    )
                    counter_row = await (await db.execute(
                        "SELECT next_cli_id FROM chat_group_cli_counters WHERE guild_id=?",
                        (int(guild_id),),
                    )).fetchone()
                next_cli = int(counter_row[0] if counter_row is not None else 1)

                for group_id in unique_group_ids:
                    if group_id in mapping:
                        continue
                    allocated_cli = next_cli
                    next_cli += 1
                    await db.execute(
                        """
                        UPDATE chat_group_cli_counters
                        SET next_cli_id=?, updated_at=?
                        WHERE guild_id=?
                        """,
                        (next_cli, now, int(guild_id)),
                    )
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO chat_group_cli_index(guild_id, group_id, cli_id, created_at)
                        VALUES(?, ?, ?, ?)
                        """,
                        (int(guild_id), group_id, allocated_cli, now),
                    )
                    row = await (await db.execute(
                        """
                        SELECT cli_id
                        FROM chat_group_cli_index
                        WHERE guild_id=? AND group_id=?
                        """,
                        (int(guild_id), group_id),
                    )).fetchone()
                    if row is None:
                        raise RuntimeError(f"failed to allocate chat-group cli_id for guild={guild_id} group={group_id}")

                cursor = await db.execute(
                    f"""
                    SELECT group_id, cli_id
                    FROM chat_group_cli_index
                    WHERE guild_id=? AND group_id IN ({placeholders})
                    """,
                    (int(guild_id), *unique_group_ids),
                )
                final_rows = await cursor.fetchall()
                await db.commit()
            except Exception:
                await db.rollback()
                raise
        return {str(row[0]): int(row[1]) for row in final_rows}

    async def list_chat_group_active_connections(self, group_id: str) -> list[ChatGroupConnectionRow]:
        memberships = await self.list_chat_group_memberships(group_id)
        active_guilds = {row.guild_id for row in memberships if row.status == "active"}
        rows = await self.list_chat_group_connections(group_id)
        return [row for row in rows if row.guild_id in active_guilds]

    def _tenant_key(self, scope_type: str, scope_id: int) -> str:
        if scope_type == "root":
            return "r0"
        if scope_type != "guild":
            raise ValueError(f"unsupported scope_type: {scope_type}")
        return f"g{int(scope_id)}"

    def _scope_from_scope_id(self, scope_id: int) -> tuple[str, int]:
        if int(scope_id) == 0:
            return "root", 0
        return "guild", int(scope_id)

    def _table_name(self, tenant_key: str, logical_name: str) -> str:
        if logical_name not in self._TENANT_TABLES:
            raise ValueError(f"unsupported logical table: {logical_name}")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", logical_name):
            raise ValueError("invalid logical table")
        if not re.fullmatch(r"[gr][0-9]+", tenant_key):
            raise ValueError("invalid tenant key")
        return f"t_{tenant_key}__{logical_name}"

    async def _touch_tenant_registry(self, scope_type: str, scope_id: int, db: aiosqlite.Connection) -> str:
        tenant_key = self._tenant_key(scope_type, scope_id)
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """
            INSERT INTO tenant_registry(tenant_key, scope_type, scope_id, created_at, last_seen_at, schema_version, migration_phase)
            VALUES(?, ?, ?, ?, ?, 1, 'cutover')
            ON CONFLICT(tenant_key)
            DO UPDATE SET last_seen_at=excluded.last_seen_at, scope_type=excluded.scope_type, scope_id=excluded.scope_id
            """,
            (tenant_key, scope_type, int(scope_id), now, now),
        )
        return tenant_key

    async def _ensure_sqlite_tenant_tables(self, scope_type: str, scope_id: int, db: aiosqlite.Connection | None = None) -> str:
        if self.backend != "sqlite":
            return self._tenant_key(scope_type, scope_id)
        owns = db is None
        if db is None:
            db = await aiosqlite.connect(self._sqlite_path)
        tenant_key = await self._touch_tenant_registry(scope_type, scope_id, db)
        configs_table = self._table_name(tenant_key, "configs")
        audit_table = self._table_name(tenant_key, "audit_logs")
        system_table = self._table_name(tenant_key, "system_logs")
        crash_table = self._table_name(tenant_key, "crash_logs")
        level_tables_table = self._table_name(tenant_key, "level_tables")
        level_users_table = self._table_name(tenant_key, "level_users")
        level_runtime_table = self._table_name(tenant_key, "level_runtime")
        level_event_logs_table = self._table_name(tenant_key, "level_event_logs")
        utility_webhooks_table = self._table_name(tenant_key, "utility_webhooks")
        sticky_runtime_table = self._table_name(tenant_key, "sticky_runtime")
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {configs_table} (
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
            f"""
            CREATE TABLE IF NOT EXISTS {audit_table} (
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
            f"""
            CREATE TABLE IF NOT EXISTS {system_table} (
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
            f"""
            CREATE TABLE IF NOT EXISTS {crash_table} (
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
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {level_tables_table} (
                guild_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                required_total_xp INTEGER NOT NULL,
                delta_xp INTEGER NOT NULL,
                segment TEXT NOT NULL,
                rebuilt_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, level)
            );
            """
        )
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {level_users_table} (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                total_xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, user_id)
            );
            """
        )
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {level_runtime_table} (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_message_at TEXT,
                last_reaction_at TEXT,
                voice_joined_at TEXT,
                last_voice_grant_at TEXT,
                PRIMARY KEY(guild_id, user_id)
            );
            """
        )
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {level_event_logs_table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                applied_xp INTEGER NOT NULL,
                total_xp INTEGER NOT NULL,
                level INTEGER NOT NULL,
                reason TEXT NOT NULL,
                detail_json TEXT
            );
            """
        )
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {utility_webhooks_table} (
                ref_id TEXT PRIMARY KEY,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                webhook_id INTEGER NOT NULL,
                webhook_token TEXT NOT NULL,
                tag TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {sticky_runtime_table} (
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                signature TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(guild_id, channel_id)
            );
            """
        )
        if owns:
            await db.commit()
            await db.close()
        return tenant_key

    async def _maybe_migrate_sqlite_legacy(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("SELECT COUNT(*) FROM tenant_registry")
        registry_count = (await cursor.fetchone())[0]
        if registry_count > 0:
            return

        async def table_exists(table_name: str) -> bool:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            return (await cur.fetchone()) is not None

        now = datetime.now(timezone.utc).isoformat()
        if await table_exists("configs"):
            cur = await db.execute("SELECT scope_type, scope_id, section, data_json, version, updated_at FROM configs")
            for row in await cur.fetchall():
                scope_type = str(row[0])
                scope_id = int(row[1])
                tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
                target = self._table_name(tenant_key, "configs")
                await db.execute(
                    f"""
                    INSERT OR IGNORE INTO {target}(scope_type, scope_id, section, data_json, version, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (scope_type, scope_id, row[2], row[3], row[4], row[5]),
                )

        if await table_exists("audit_logs"):
            cur = await db.execute(
                "SELECT at, actor_user_id, scope_type, scope_id, section, action, before_json, after_json, result FROM audit_logs"
            )
            for row in await cur.fetchall():
                scope_type = str(row[2])
                scope_id = int(row[3])
                tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
                target = self._table_name(tenant_key, "audit_logs")
                await db.execute(
                    f"""
                    INSERT INTO {target}(at, actor_user_id, scope_type, scope_id, section, action, before_json, after_json, result)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )

        if await table_exists("system_logs"):
            cur = await db.execute(
                "SELECT at, actor_user_id, scope_id, feature, severity, message, detail_json FROM system_logs"
            )
            for row in await cur.fetchall():
                scope_type, scope_id = self._scope_from_scope_id(int(row[2]))
                tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
                target = self._table_name(tenant_key, "system_logs")
                await db.execute(
                    f"""
                    INSERT INTO {target}(at, actor_user_id, scope_id, feature, severity, message, detail_json)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )

        if await table_exists("crash_logs"):
            cur = await db.execute(
                "SELECT error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status FROM crash_logs"
            )
            for row in await cur.fetchall():
                scope_type = str(row[2])
                scope_id = int(row[3])
                tenant_key = await self._ensure_sqlite_tenant_tables(scope_type, scope_id, db=db)
                target = self._table_name(tenant_key, "crash_logs")
                await db.execute(
                    f"""
                    INSERT OR IGNORE INTO {target}(error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )

        if await table_exists("utility_webhooks"):
            cur = await db.execute(
                "SELECT ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at FROM utility_webhooks"
            )
            for row in await cur.fetchall():
                guild_id = int(row[1])
                tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
                target = self._table_name(tenant_key, "utility_webhooks")
                await db.execute(
                    f"""
                    INSERT OR REPLACE INTO {target}(ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                await db.execute(
                    """
                    INSERT OR REPLACE INTO utility_webhook_index(ref_id, tenant_key, guild_id, created_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (row[0], tenant_key, guild_id, now),
                )

        for logical_name in ("level_tables", "level_users", "level_runtime", "level_event_logs"):
            if not await table_exists(logical_name):
                continue
            cur = await db.execute(f"SELECT * FROM {logical_name}")
            rows = await cur.fetchall()
            for row in rows:
                guild_id = int(row[0] if logical_name != "level_event_logs" else row[2])
                tenant_key = await self._ensure_sqlite_tenant_tables("guild", guild_id, db=db)
                target = self._table_name(tenant_key, logical_name)
                placeholders = ",".join(["?"] * len(row))
                columns_cur = await db.execute(f"PRAGMA table_info({logical_name})")
                columns = [col[1] for col in await columns_cur.fetchall()]
                cols = ",".join(columns)
                await db.execute(f"INSERT OR IGNORE INTO {target}({cols}) VALUES({placeholders})", row)
        await db.commit()

    async def _ensure_pg_pool(self) -> None:
        if self._pg_pool is not None:
            return
        import asyncpg

        self._pg_pool = await asyncpg.create_pool(self.database_url)

    async def _touch_tenant_registry_pg(self, conn: Any, scope_type: str, scope_id: int) -> str:
        tenant_key = self._tenant_key(scope_type, scope_id)
        await conn.execute(
            """
            INSERT INTO tenant_registry(tenant_key, scope_type, scope_id, created_at, last_seen_at, schema_version, migration_phase)
            VALUES($1, $2, $3, NOW(), NOW(), 1, 'cutover')
            ON CONFLICT(tenant_key)
            DO UPDATE SET last_seen_at=NOW(), scope_type=EXCLUDED.scope_type, scope_id=EXCLUDED.scope_id
            """,
            tenant_key,
            scope_type,
            int(scope_id),
        )
        return tenant_key

    async def _ensure_pg_tenant_tables(self, scope_type: str, scope_id: int, conn: Any) -> str:
        tenant_key = await self._touch_tenant_registry_pg(conn, scope_type, scope_id)
        configs_table = self._table_name(tenant_key, "configs")
        audit_table = self._table_name(tenant_key, "audit_logs")
        system_table = self._table_name(tenant_key, "system_logs")
        crash_table = self._table_name(tenant_key, "crash_logs")
        level_tables_table = self._table_name(tenant_key, "level_tables")
        level_users_table = self._table_name(tenant_key, "level_users")
        level_runtime_table = self._table_name(tenant_key, "level_runtime")
        level_event_logs_table = self._table_name(tenant_key, "level_event_logs")
        utility_webhooks_table = self._table_name(tenant_key, "utility_webhooks")
        sticky_runtime_table = self._table_name(tenant_key, "sticky_runtime")
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {configs_table} (
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
            f"""
            CREATE TABLE IF NOT EXISTS {audit_table} (
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
            f"""
            CREATE TABLE IF NOT EXISTS {system_table} (
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
            f"""
            CREATE TABLE IF NOT EXISTS {crash_table} (
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
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {level_tables_table} (
                guild_id BIGINT NOT NULL,
                level INT NOT NULL,
                required_total_xp BIGINT NOT NULL,
                delta_xp BIGINT NOT NULL,
                segment TEXT NOT NULL,
                rebuilt_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(guild_id, level)
            );
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {level_users_table} (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                total_xp BIGINT NOT NULL DEFAULT 0,
                level INT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(guild_id, user_id)
            );
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {level_runtime_table} (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                last_message_at TIMESTAMPTZ,
                last_reaction_at TIMESTAMPTZ,
                voice_joined_at TIMESTAMPTZ,
                last_voice_grant_at TIMESTAMPTZ,
                PRIMARY KEY(guild_id, user_id)
            );
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {level_event_logs_table} (
                id BIGSERIAL PRIMARY KEY,
                at TIMESTAMPTZ NOT NULL,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                event_type TEXT NOT NULL,
                applied_xp BIGINT NOT NULL,
                total_xp BIGINT NOT NULL,
                level INT NOT NULL,
                reason TEXT NOT NULL,
                detail_json JSONB
            );
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {utility_webhooks_table} (
                ref_id TEXT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                webhook_id BIGINT NOT NULL,
                webhook_token TEXT NOT NULL,
                tag TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            );
            """
        )
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {sticky_runtime_table} (
                guild_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                signature TEXT,
                updated_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY(guild_id, channel_id)
            );
            """
        )
        return tenant_key

    async def _maybe_migrate_pg_legacy(self, conn: Any) -> None:
        registry_count = await conn.fetchval("SELECT COUNT(*) FROM tenant_registry")
        if int(registry_count or 0) > 0:
            return

        scopes: set[tuple[str, int]] = {("root", 0)}
        for row in await conn.fetch("SELECT DISTINCT scope_type, scope_id FROM configs"):
            scopes.add((str(row["scope_type"]), int(row["scope_id"])))
        for row in await conn.fetch("SELECT DISTINCT scope_type, scope_id FROM audit_logs"):
            scopes.add((str(row["scope_type"]), int(row["scope_id"])))
        for row in await conn.fetch("SELECT DISTINCT scope_type, scope_id FROM crash_logs"):
            scopes.add((str(row["scope_type"]), int(row["scope_id"])))
        for row in await conn.fetch("SELECT DISTINCT scope_id FROM system_logs"):
            scope_id = int(row["scope_id"])
            scopes.add(self._scope_from_scope_id(scope_id))
        for table in ("level_tables", "level_users", "level_runtime", "level_event_logs", "utility_webhooks"):
            for row in await conn.fetch(f"SELECT DISTINCT guild_id FROM {table}"):
                scopes.add(("guild", int(row["guild_id"])))

        for scope_type, scope_id in sorted(scopes, key=lambda item: (item[0], item[1])):
            tenant_key = await self._ensure_pg_tenant_tables(scope_type, scope_id, conn=conn)
            configs_table = self._table_name(tenant_key, "configs")
            audit_table = self._table_name(tenant_key, "audit_logs")
            system_table = self._table_name(tenant_key, "system_logs")
            crash_table = self._table_name(tenant_key, "crash_logs")
            level_tables_table = self._table_name(tenant_key, "level_tables")
            level_users_table = self._table_name(tenant_key, "level_users")
            level_runtime_table = self._table_name(tenant_key, "level_runtime")
            level_event_logs_table = self._table_name(tenant_key, "level_event_logs")
            utility_webhooks_table = self._table_name(tenant_key, "utility_webhooks")

            await conn.execute(
                f"""
                INSERT INTO {configs_table}(scope_type, scope_id, section, data_json, version, updated_at)
                SELECT scope_type, scope_id, section, data_json, version, updated_at
                FROM configs
                WHERE scope_type=$1 AND scope_id=$2
                ON CONFLICT(scope_type, scope_id, section) DO NOTHING
                """,
                scope_type,
                scope_id,
            )
            await conn.execute(
                f"""
                INSERT INTO {audit_table}(at, actor_user_id, scope_type, scope_id, section, action, before_json, after_json, result)
                SELECT at, actor_user_id, scope_type, scope_id, section, action, before_json, after_json, result
                FROM audit_logs
                WHERE scope_type=$1 AND scope_id=$2
                """,
                scope_type,
                scope_id,
            )
            await conn.execute(
                f"""
                INSERT INTO {system_table}(at, actor_user_id, scope_id, feature, severity, message, detail_json)
                SELECT at, actor_user_id, scope_id, feature, severity, message, detail_json
                FROM system_logs
                WHERE scope_id=$1
                """,
                scope_id,
            )
            await conn.execute(
                f"""
                INSERT INTO {crash_table}(error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status)
                SELECT error_id, at, scope_type, scope_id, actor_user_id, section, command, message, traceback, context_json, forward_mode, forward_status
                FROM crash_logs
                WHERE scope_type=$1 AND scope_id=$2
                ON CONFLICT(error_id) DO NOTHING
                """,
                scope_type,
                scope_id,
            )
            if scope_type == "guild":
                await conn.execute(
                    f"""
                    INSERT INTO {level_tables_table}(guild_id, level, required_total_xp, delta_xp, segment, rebuilt_at)
                    SELECT guild_id, level, required_total_xp, delta_xp, segment, rebuilt_at
                    FROM level_tables
                    WHERE guild_id=$1
                    ON CONFLICT(guild_id, level) DO NOTHING
                    """,
                    scope_id,
                )
                await conn.execute(
                    f"""
                    INSERT INTO {level_users_table}(guild_id, user_id, total_xp, level, updated_at)
                    SELECT guild_id, user_id, total_xp, level, updated_at
                    FROM level_users
                    WHERE guild_id=$1
                    ON CONFLICT(guild_id, user_id) DO NOTHING
                    """,
                    scope_id,
                )
                await conn.execute(
                    f"""
                    INSERT INTO {level_runtime_table}(guild_id, user_id, last_message_at, last_reaction_at, voice_joined_at, last_voice_grant_at)
                    SELECT guild_id, user_id, last_message_at, last_reaction_at, voice_joined_at, last_voice_grant_at
                    FROM level_runtime
                    WHERE guild_id=$1
                    ON CONFLICT(guild_id, user_id) DO NOTHING
                    """,
                    scope_id,
                )
                await conn.execute(
                    f"""
                    INSERT INTO {level_event_logs_table}(at, guild_id, user_id, event_type, applied_xp, total_xp, level, reason, detail_json)
                    SELECT at, guild_id, user_id, event_type, applied_xp, total_xp, level, reason, detail_json
                    FROM level_event_logs
                    WHERE guild_id=$1
                    """,
                    scope_id,
                )
                await conn.execute(
                    f"""
                    INSERT INTO {utility_webhooks_table}(ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at)
                    SELECT ref_id, guild_id, channel_id, webhook_id, webhook_token, tag, created_at
                    FROM utility_webhooks
                    WHERE guild_id=$1
                    ON CONFLICT(ref_id) DO NOTHING
                    """,
                    scope_id,
                )
                await conn.execute(
                    """
                    INSERT INTO utility_webhook_index(ref_id, tenant_key, guild_id, created_at)
                    SELECT ref_id, $1, guild_id, created_at
                    FROM utility_webhooks
                    WHERE guild_id=$2
                    ON CONFLICT(ref_id) DO NOTHING
                    """,
                    tenant_key,
                    scope_id,
                )

    def _new_error_id(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        suffix = uuid.uuid4().hex[:12]
        return f"CR-{now}-{suffix}"

    def _extract_running_payload(self, raw: dict[str, Any]) -> dict[str, Any]:
        payload = raw.get("payload", raw) if isinstance(raw, dict) else {}
        if isinstance(payload, dict) and isinstance(payload.get("running_payload"), dict):
            return dict(payload["running_payload"])
        if isinstance(payload, dict):
            return dict(payload)
        return {}
