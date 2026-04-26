from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from utils.cli.engine import CliEngine
from utils.cli.types import EngineContext, ScopeType, SessionContext
from utils.storage import Storage
from utils.tick import TickMeter


def _is_root_section(section_key: str) -> bool:
    return (
        section_key in {"root-defaults", "root-enforce", "root-enforce-override"}
        or section_key.startswith("root-defaults/")
        or section_key.startswith("root-enforce/")
        or section_key.startswith("root-enforce-override/")
        or section_key.startswith("tenant-connection/")
    )

async def _safe_insert_system_log(
    storage: Storage,
    *,
    actor_user_id: int | None,
    scope_id: int,
    feature: str,
    severity: str,
    message: str,
    detail_json: dict[str, Any],
) -> None:
    await storage.insert_system_log_safe(
        actor_user_id=actor_user_id,
        scope_id=scope_id,
        feature=feature,
        severity=severity,
        message=message,
        detail_json=detail_json,
    )


async def bind_all_settings(storage: Storage, guild_ids: list[int], tick_meter: TickMeter | None = None) -> None:
    await _safe_insert_system_log(
        storage,
        actor_user_id=None,
        scope_id=0,
        feature="config-bind",
        severity="info",
        message="bind-started",
        detail_json={"guild_count": len(guild_ids)},
    )
    if tick_meter is not None:
        await tick_meter.consume(0, "log.system.write", amount=1, stoppable=False)
    completed_guilds: set[int] = set()
    phase = "root-bind"
    failed_guild_id: int | None = None
    try:
        engine = CliEngine(storage)
        await _bind_root_sections(engine)
        phase = "guild-bind"
        for guild_id in guild_ids:
            current_guild = int(guild_id)
            failed_guild_id = current_guild
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=current_guild,
                feature="config-bind",
                severity="info",
                message="bind-started",
                detail_json={"guild_id": current_guild, "mode": "startup-all"},
            )
            if tick_meter is not None:
                await tick_meter.consume(current_guild, "log.system.write", amount=1, stoppable=False)
            await _bind_guild_sections(engine, current_guild)
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=current_guild,
                feature="config-bind",
                severity="info",
                message="bind-completed",
                detail_json={"guild_id": current_guild, "mode": "startup-all"},
            )
            if tick_meter is not None:
                await tick_meter.consume(current_guild, "log.system.write", amount=1, stoppable=False)
            completed_guilds.add(current_guild)
            failed_guild_id = None
    except Exception as exc:
        trace = traceback.format_exc()
        trace_preview = trace[:2048]
        detail_base = {
            "phase": phase,
            "error_type": exc.__class__.__name__,
            "error_message": str(exc),
            "traceback": trace_preview,
        }
        if failed_guild_id is not None:
            detail_base["failed_guild_id"] = failed_guild_id
        await _safe_insert_system_log(
            storage,
            actor_user_id=None,
            scope_id=0,
            feature="config-bind",
            severity="error",
            message="bind-failed",
            detail_json=detail_base,
        )
        if tick_meter is not None:
            await tick_meter.consume(0, "log.system.write", amount=1, stoppable=False)

        for guild_id in guild_ids:
            current_guild = int(guild_id)
            if current_guild in completed_guilds:
                continue
            status = "skipped_due_to_global_failure"
            if failed_guild_id is not None and current_guild == failed_guild_id:
                status = "failed_current_guild"
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=current_guild,
                feature="config-bind",
                severity="error",
                message="bind-failed",
                detail_json={
                    **detail_base,
                    "guild_id": current_guild,
                    "mode": "startup-all",
                    "status": status,
                },
            )
            if tick_meter is not None:
                await tick_meter.consume(current_guild, "log.system.write", amount=1, stoppable=False)
        raise
    await _safe_insert_system_log(
        storage,
        actor_user_id=None,
        scope_id=0,
        feature="config-bind",
        severity="info",
        message="bind-completed",
        detail_json={"guild_count": len(guild_ids)},
    )
    if tick_meter is not None:
        await tick_meter.consume(0, "log.system.write", amount=1, stoppable=False)


@dataclass
class BulkOperationResult:
    total: int
    success: int
    failed: int
    details: list[str]


def list_guild_sections(engine: CliEngine) -> list[str]:
    return sorted(section_key for section_key in engine.sections.keys() if not _is_root_section(section_key))


def root_diff_sections(
    root_defaults: dict[str, Any] | None,
    root_enforce: dict[str, Any] | None,
    root_override: dict[str, Any] | None,
    guild_id: int,
) -> set[str]:
    sections: set[str] = set()
    for payload in (root_defaults, root_enforce):
        block = payload.get("sections", {}) if isinstance(payload, dict) else {}
        if isinstance(block, dict):
            sections.update(str(key) for key in block.keys())

    if isinstance(root_override, dict):
        guilds = root_override.get("guilds", {})
        if isinstance(guilds, dict):
            guild_entry = guilds.get(str(guild_id), {})
            if isinstance(guild_entry, dict):
                override_sections = guild_entry.get("sections", {})
                if isinstance(override_sections, dict):
                    sections.update(str(key) for key in override_sections.keys())
    return sections


async def bind_sections(storage: Storage, guild_id: int, sections: list[str]) -> None:
    scope_id = int(guild_id)
    engine = CliEngine(storage)
    await _bind_root_sections(engine)
    guild_session = SessionContext(
        session_id=f"bind-guild-sections-{scope_id}-{uuid.uuid4()}",
        guild_id=scope_id,
        thread_id=0,
        actor_user_id=0,
        scope_type=ScopeType.GUILD,
        scope_id=scope_id,
    )
    for section_key in sections:
        if section_key not in engine.sections:
            continue
        if _is_root_section(section_key):
            continue
        await engine._ensure_config_state(guild_session, section_key)


async def rebind_guild(storage: Storage, guild_id: int, mode: Literal["root-diff", "full"]) -> list[str]:
    scope_id = int(guild_id)
    engine = CliEngine(storage)
    await _bind_root_sections(engine)
    all_sections = list_guild_sections(engine)
    selected_sections = all_sections
    if mode == "root-diff":
        root_defaults = await storage.load_config("root", 0, "root-defaults")
        root_enforce = await storage.load_config("root", 0, "root-enforce")
        root_override = await storage.load_config("root", 0, "root-enforce-override")
        default_payload = root_defaults.data.get("payload", {}).get("running_payload", {}) if root_defaults else {}
        enforce_payload = root_enforce.data.get("payload", {}).get("running_payload", {}) if root_enforce else {}
        override_payload = root_override.data.get("payload", {}).get("running_payload", {}) if root_override else {}
        impacted = root_diff_sections(default_payload, enforce_payload, override_payload, scope_id)
        if impacted:
            selected_sections = [section for section in all_sections if section in impacted]
        else:
            selected_sections = []

    guild_session = SessionContext(
        session_id=f"rebind-guild-{scope_id}-{uuid.uuid4()}",
        guild_id=scope_id,
        thread_id=0,
        actor_user_id=0,
        scope_type=ScopeType.GUILD,
        scope_id=scope_id,
    )
    for section_key in selected_sections:
        await engine._ensure_config_state(guild_session, section_key)
    return selected_sections


async def rebind_many_guilds(storage: Storage, guild_ids: list[int], mode: Literal["root-diff", "full"]) -> BulkOperationResult:
    details: list[str] = []
    success = 0
    failed = 0
    for guild_id in guild_ids:
        scope_id = int(guild_id)
        try:
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=scope_id,
                feature="config-rebind",
                severity="info",
                message="rebind-started",
                detail_json={"mode": mode, "guild_id": scope_id},
            )
            sections = await rebind_guild(storage, scope_id, mode)
            success += 1
            details.append(f"guild={scope_id} status=ok sections={len(sections)}")
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=scope_id,
                feature="config-rebind",
                severity="info",
                message="rebind-completed",
                detail_json={"mode": mode, "guild_id": scope_id, "sections": len(sections)},
            )
        except Exception as exc:
            failed += 1
            details.append(f"guild={scope_id} status=failed reason={exc.__class__.__name__}:{exc}")
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=scope_id,
                feature="config-rebind",
                severity="error",
                message="rebind-failed",
                detail_json={"mode": mode, "guild_id": scope_id, "error": str(exc), "error_type": exc.__class__.__name__},
            )
    return BulkOperationResult(total=len(guild_ids), success=success, failed=failed, details=details)


async def deploy_guild(storage: Storage, guild_id: int) -> list[str]:
    scope_id = int(guild_id)
    engine = CliEngine(storage)
    ctx = EngineContext(
        actor_user_id=0,
        guild_id=scope_id,
        channel_id=0,
        is_bot_admin=True,
        has_manage_guild=True,
    )
    session = SessionContext(
        session_id=f"deploy-guild-{scope_id}-{uuid.uuid4()}",
        guild_id=scope_id,
        thread_id=0,
        actor_user_id=0,
        scope_type=ScopeType.GUILD,
        scope_id=scope_id,
    )
    for section_key in list_guild_sections(engine):
        await engine._ensure_config_state(session, section_key)
    _, result = await engine._cmd_deploy(ctx, session, [])
    changed = []
    if result.output.startswith("deployed startup: "):
        changed = [chunk.strip() for chunk in result.output.replace("deployed startup: ", "", 1).split(",") if chunk.strip()]
    return changed


async def deploy_many_guilds(storage: Storage, guild_ids: list[int]) -> BulkOperationResult:
    details: list[str] = []
    success = 0
    failed = 0
    for guild_id in guild_ids:
        scope_id = int(guild_id)
        try:
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=scope_id,
                feature="config-deploy",
                severity="info",
                message="deploy-started",
                detail_json={"guild_id": scope_id},
            )
            changed = await deploy_guild(storage, scope_id)
            success += 1
            details.append(f"guild={scope_id} status=ok sections={len(changed)}")
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=scope_id,
                feature="config-deploy",
                severity="info",
                message="deploy-completed",
                detail_json={"guild_id": scope_id, "sections": len(changed)},
            )
        except Exception as exc:
            failed += 1
            details.append(f"guild={scope_id} status=failed reason={exc.__class__.__name__}:{exc}")
            await _safe_insert_system_log(
                storage,
                actor_user_id=None,
                scope_id=scope_id,
                feature="config-deploy",
                severity="error",
                message="deploy-failed",
                detail_json={"guild_id": scope_id, "error": str(exc), "error_type": exc.__class__.__name__},
            )
    return BulkOperationResult(total=len(guild_ids), success=success, failed=failed, details=details)


async def bind_single_guild(storage: Storage, guild_id: int) -> None:
    scope_id = int(guild_id)
    phase = "root-bind"
    await _safe_insert_system_log(
        storage,
        actor_user_id=None,
        scope_id=scope_id,
        feature="config-bind",
        severity="info",
        message="bind-started",
        detail_json={"guild_id": scope_id},
    )
    try:
        engine = CliEngine(storage)
        await _bind_root_sections(engine)
        phase = "guild-bind"
        await _bind_guild_sections(engine, scope_id)
    except Exception as exc:
        trace = traceback.format_exc()
        trace_preview = trace[:2048]
        await _safe_insert_system_log(
            storage,
            actor_user_id=None,
            scope_id=scope_id,
            feature="config-bind",
            severity="error",
            message="bind-failed",
            detail_json={
                "guild_id": scope_id,
                "phase": phase,
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
                "traceback": trace_preview,
            },
        )
        raise
    await _safe_insert_system_log(
        storage,
        actor_user_id=None,
        scope_id=scope_id,
        feature="config-bind",
        severity="info",
        message="bind-completed",
        detail_json={"guild_id": scope_id},
    )


async def _bind_root_sections(engine: CliEngine) -> None:
    root_ctx = EngineContext(
        actor_user_id=0,
        guild_id=0,
        channel_id=0,
        is_bot_admin=True,
        has_manage_guild=True,
    )
    root_session = SessionContext(
        session_id=f"bind-root-{uuid.uuid4()}",
        guild_id=0,
        thread_id=0,
        actor_user_id=0,
        scope_type=ScopeType.ROOT,
        scope_id=0,
    )
    _ = root_ctx
    for section_key in sorted(engine.sections.keys()):
        if not _is_root_section(section_key):
            continue
        await engine._ensure_config_state(root_session, section_key)


async def _bind_guild_sections(engine: CliEngine, guild_id: int) -> None:
    guild_session = SessionContext(
        session_id=f"bind-guild-{guild_id}-{uuid.uuid4()}",
        guild_id=guild_id,
        thread_id=0,
        actor_user_id=0,
        scope_type=ScopeType.GUILD,
        scope_id=guild_id,
    )
    for section_key in sorted(engine.sections.keys()):
        if _is_root_section(section_key):
            continue
        await engine._ensure_config_state(guild_session, section_key)
