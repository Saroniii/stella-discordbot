from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from utils.cli.logs import resolve_severity
from utils.cli.parser import ParseError, parse_line
from utils.cli.sections import build_section_registry
from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.types import ConfigEnvelope, EngineContext, EngineResult, ScopeType, SessionContext
from utils.storage import ReceiveConfig, Storage


HELP_TEXT = """commands:
  help [cmd]
  where | top | quit
  enter <section> | leave | select <id>
  set <key> <value...> | unset <key> | show | deploy | discard
  get counters all
  get log audit [limit] | get log system [limit] | get log crash [limit|error_id]
  diagnose database
  switch root | switch guild <guild-id>
"""


TRACKED_COMMANDS = {
    "set",
    "unset",
    "show",
    "deploy",
    "discard",
    "get",
    "diagnose",
    "enter",
    "leave",
    "select",
    "switch",
}


@dataclass
class CounterEntry:
    ok_count: int = 0
    error_count: int = 0
    last_error_at: str | None = None
    last_error_id: str | None = None


class CliEngine:
    def __init__(
        self,
        storage: Storage,
        crash_notifier: Callable[[int, str], Awaitable[str]] | None = None,
    ) -> None:
        self.storage = storage
        self.sections = build_section_registry()
        self.crash_notifier = crash_notifier
        self.counters: dict[tuple[int, str, str], CounterEntry] = {}

    async def initialize_session(self, ctx: EngineContext) -> tuple[SessionContext, EngineResult]:
        session = SessionContext(
            session_id=str(uuid.uuid4()),
            guild_id=ctx.guild_id,
            thread_id=ctx.channel_id,
            actor_user_id=ctx.actor_user_id,
            scope_type=ScopeType.GUILD,
            scope_id=ctx.guild_id,
        )
        output = "CLI session started. use `help` for commands."
        return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

    async def execute(self, ctx: EngineContext, session: SessionContext, line: str) -> tuple[SessionContext, EngineResult]:
        parsed = parse_line(line)
        if parsed is None:
            return session, EngineResult(output="", prompt=self._prompt(session), should_exit=False)

        if isinstance(parsed, ParseError):
            self._record_counter(session.scope_id, "global", "parse", ok=False)
            return session, EngineResult(output=f"parse error: {parsed.reason}", prompt=self._prompt(session), should_exit=False)

        command = parsed.name
        args = parsed.args

        handlers = {
            "help": self._cmd_help,
            "where": self._cmd_where,
            "top": self._cmd_top,
            "quit": self._cmd_quit,
            "enter": self._cmd_enter,
            "leave": self._cmd_leave,
            "select": self._cmd_select,
            "set": self._cmd_set,
            "unset": self._cmd_unset,
            "show": self._cmd_show,
            "deploy": self._cmd_deploy,
            "discard": self._cmd_discard,
            "get": self._cmd_get,
            "diagnose": self._cmd_diagnose,
            "switch": self._cmd_switch,
        }

        handler = handlers.get(command)
        if not handler:
            self._record_counter(session.scope_id, self._counter_section(session), command, ok=False)
            return session, EngineResult(output=f"unknown command: {command}", prompt=self._prompt(session), should_exit=False)

        try:
            updated_session, result = await handler(ctx, session, args)
            if command in TRACKED_COMMANDS:
                self._record_counter(updated_session.scope_id, self._counter_section(updated_session), command, ok=True)
            return updated_session, result
        except SectionError as exc:
            if command in TRACKED_COMMANDS:
                self._record_counter(session.scope_id, self._counter_section(session), command, ok=False)
            return session, EngineResult(output=str(exc), prompt=self._prompt(session), should_exit=False)
        except Exception as exc:
            error_id = await self._handle_crash(ctx, session, command, args, exc)
            if command in TRACKED_COMMANDS:
                self._record_counter(session.scope_id, self._counter_section(session), command, ok=False, error_id=error_id)
            return session, EngineResult(output=f"fatal error: error_id={error_id}", prompt=self._prompt(session), should_exit=False)

    async def _cmd_help(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        return session, EngineResult(output=HELP_TEXT, prompt=self._prompt(session), should_exit=False)

    async def _cmd_where(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        path = "/".join(session.current_path) if session.current_path else "(top)"
        output = f"scope={session.scope_type.value}:{session.scope_id} path={path}"
        return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

    async def _cmd_top(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        session.current_path = []
        session.selected_object = None
        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_quit(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        return session, EngineResult(output="session closed", prompt=self._prompt(session), should_exit=True)

    async def _cmd_enter(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) != 1:
            raise SectionError("field=enter reason=invalid args hint=enter <section>")

        target = args[0].lower()
        if not session.current_path:
            if target in {"guild-log", "control-plane", "tenant-connection"}:
                if target == "tenant-connection" and session.scope_type != ScopeType.ROOT:
                    raise SectionError("field=section reason=forbidden hint=tenant-connection is root-only")
                if target == "control-plane" and session.scope_type != ScopeType.GUILD:
                    raise SectionError("field=section reason=forbidden hint=control-plane is guild-only")
                session.current_path = [target]
                return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

            if target in {"root-defaults", "root-enforce"}:
                if not ctx.is_bot_admin or session.scope_type != ScopeType.ROOT:
                    raise SectionError("field=section reason=forbidden hint=root sections require root scope and bot admin")

            if target in {"welcome", "log-config", "log-type"} and session.scope_type != ScopeType.GUILD:
                raise SectionError("field=section reason=forbidden hint=guild section requires guild scope")

            if target not in {"welcome", "log-config", "log-type", "root-defaults", "root-enforce"}:
                raise SectionError("field=section reason=unknown section hint=use help")

            session.current_path = [target]
            await self._ensure_committed(session, self._current_section_key(session))
            return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

        if session.current_path == ["guild-log"]:
            if target not in {"mod-log", "message-log", "member-log"}:
                raise SectionError("field=section reason=unknown subsection hint=mod-log|message-log|member-log")
            session.current_path = ["guild-log", target]
            await self._ensure_committed(session, self._current_section_key(session))
            return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

        if session.current_path == ["control-plane"]:
            if target != "root-connection":
                raise SectionError("field=section reason=unknown subsection hint=root-connection")
            session.current_path = ["control-plane", "root-connection"]
            await self._ensure_committed(session, self._current_section_key(session))
            return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

        if session.current_path == ["tenant-connection"]:
            if target != "log":
                raise SectionError("field=section reason=unknown subsection hint=log")
            session.current_path = ["tenant-connection", "log"]
            await self._ensure_committed(session, self._current_section_key(session))
            return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

        raise SectionError("field=enter reason=invalid context hint=use leave or top")

    async def _cmd_leave(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if session.current_path:
            session.current_path = session.current_path[:-1]
        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_select(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) != 1:
            raise SectionError("field=select reason=invalid args hint=select <id>")
        session.selected_object = args[0]
        return session, EngineResult(output=f"selected {args[0]}", prompt=self._prompt(session), should_exit=False)

    async def _cmd_set(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) < 2:
            raise SectionError("field=set reason=invalid args hint=set <key> <value...>")
        section_key = self._current_section_key(session)
        if not section_key:
            raise SectionError("field=set reason=no active section hint=enter <section>")

        spec = self._section_spec(section_key)
        storage_key = self._storage_section_key(section_key)
        committed = await self._ensure_committed(session, section_key)
        current_candidate = session.candidate_map.get(storage_key, committed)

        key = args[0]
        values = args[1:]

        await self._check_enforce_guard(session, section_key, key)
        updated = spec.validate_set(current_candidate, key, values)
        session.candidate_map[storage_key] = updated

        await self._write_audit_and_system(
            ctx,
            session,
            section=storage_key,
            action="set",
            before=current_candidate,
            after=updated,
            result="ok",
            feature=section_key,
            message=f"set {key}",
        )

        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_unset(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) != 1:
            raise SectionError("field=unset reason=invalid args hint=unset <key>")
        section_key = self._current_section_key(session)
        if not section_key:
            raise SectionError("field=unset reason=no active section hint=enter <section>")

        spec = self._section_spec(section_key)
        storage_key = self._storage_section_key(section_key)
        committed = await self._ensure_committed(session, section_key)
        current_candidate = session.candidate_map.get(storage_key, committed)

        key = args[0]
        await self._check_enforce_guard(session, section_key, key)
        updated = spec.apply_unset(current_candidate, key)
        session.candidate_map[storage_key] = updated

        await self._write_audit_and_system(
            ctx,
            session,
            section=storage_key,
            action="unset",
            before=current_candidate,
            after=updated,
            result="ok",
            feature=section_key,
            message=f"unset {key}",
        )

        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_show(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        section_key = self._current_section_key(session)
        if not section_key:
            raise SectionError("field=show reason=no active section hint=enter <section>")

        spec = self._section_spec(section_key)
        storage_key = self._storage_section_key(section_key)
        committed = await self._ensure_committed(session, section_key)
        candidate = session.candidate_map.get(storage_key)
        output = spec.render_show(committed, candidate)
        return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

    async def _cmd_deploy(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if not session.candidate_map:
            return session, EngineResult(output="nothing to deploy", prompt=self._prompt(session), should_exit=False)

        changed_sections: list[str] = []
        for storage_key, payload in list(session.candidate_map.items()):
            envelope = ConfigEnvelope(schema_version=1, payload=payload).model_dump(mode="json")
            version = await self.storage.upsert_config(session.scope_type.value, session.scope_id, storage_key, envelope)
            await self.storage.insert_audit_log(
                actor_user_id=ctx.actor_user_id,
                scope_type=session.scope_type.value,
                scope_id=session.scope_id,
                section=storage_key,
                action="deploy",
                before_json=None,
                after_json=payload,
                result=f"version={version}",
            )
            changed_sections.append(f"{storage_key}@v{version}")

        session.candidate_map.clear()

        if session.scope_type == ScopeType.GUILD:
            log_cfg = await self._load_section_payload(ScopeType.GUILD, session.scope_id, "log-config")
            log_cfg = log_cfg or {}
            audit_max = int(log_cfg.get("audit_log_max_buffer", 10000))
            system_max = int(log_cfg.get("system_log_max_buffer", 10000))
            await self.storage.trim_logs(scope_id=session.scope_id, audit_max=audit_max, system_max=system_max)

        return session, EngineResult(output="deployed: " + ", ".join(changed_sections), prompt=self._prompt(session), should_exit=False)

    async def _cmd_discard(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        session.candidate_map.clear()
        await self._write_audit_and_system(
            ctx,
            session,
            section="all",
            action="discard",
            before=None,
            after=None,
            result="ok",
            feature="cli",
            message="discard candidates",
        )
        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_get(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) < 2:
            raise SectionError("field=get reason=invalid args hint=get counters all|get log audit")

        if args[0] == "counters" and args[1] == "all":
            return session, EngineResult(output=self._format_counters(session.scope_id), prompt=self._prompt(session), should_exit=False)

        if args[0] == "log" and args[1] in {"audit", "system"}:
            limit = int(args[2]) if len(args) > 2 else 50
            rows = await self.storage.fetch_logs(args[1], session.scope_id, limit)
            lines = [f"{args[1]} logs (limit={limit}):"]
            for row in rows:
                lines.append(
                    f"[{row.log_id}] at={row.at} actor={row.actor_user_id} section={row.section} action={row.action} result={row.result}"
                )
            if len(lines) == 1:
                lines.append("(empty)")
            return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)

        if args[0] == "log" and args[1] == "crash":
            scope_type = session.scope_type.value if ctx.is_bot_admin else "guild"

            if len(args) > 2:
                try:
                    limit = int(args[2])
                    error_id = None
                except ValueError:
                    limit = None
                    error_id = args[2]
            else:
                limit = 50
                error_id = None

            if error_id is not None:
                row = await self.storage.fetch_crash_log_by_error_id(scope_type=scope_type, scope_id=session.scope_id, error_id=error_id)
                if row is None:
                    return session, EngineResult(
                        output=f"crash log not found: error_id={error_id}",
                        prompt=self._prompt(session),
                        should_exit=False,
                    )
                context = row.context_json if isinstance(row.context_json, dict) else {}
                detail = [
                    f"error_id={row.error_id}",
                    f"at={row.at}",
                    f"scope={row.scope_type}:{row.scope_id}",
                    f"actor={row.actor_user_id}",
                    f"section={row.section}",
                    f"command={row.command}",
                    f"args={context.get('args', [])}",
                    f"path={context.get('current_path', [])}",
                    f"message={row.message}",
                    f"forward_mode={row.forward_mode}",
                    f"forward_status={row.forward_status}",
                    "traceback:",
                    row.traceback,
                ]
                return session, EngineResult(output="\n".join(detail), prompt=self._prompt(session), should_exit=False)

            rows = await self.storage.fetch_crash_logs(scope_type=scope_type, scope_id=session.scope_id, limit=limit)
            lines = [f"crash logs ({scope_type}) (limit={limit}):"]
            for row in rows:
                context = row.context_json if isinstance(row.context_json, dict) else {}
                scope_text = f"{context.get('scope_type', row.scope_type)}:{context.get('scope_id', row.scope_id)}"
                actor = context.get("actor_user_id", row.actor_user_id)
                path_values = context.get("current_path", [])
                if isinstance(path_values, list):
                    path_text = "/".join(str(value) for value in path_values) if path_values else "(top)"
                else:
                    path_text = str(path_values)
                cmd_args = context.get("args", [])
                lines.append(
                    f"[{row.error_id}] at={row.at} scope={scope_text} actor={actor} "
                    f"section={row.section} command={row.command} args={cmd_args} "
                    f"path={path_text} message={row.message} forward={row.forward_status}"
                )
            if len(lines) == 1:
                lines.append("(empty)")
            return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)

        section_name = args[0]
        target = args[1]
        spec = self.sections.get(section_name)
        if not spec:
            raise SectionError("field=section reason=unknown section hint=use help")
        return session, EngineResult(output=spec.handle_get(target), prompt=self._prompt(session), should_exit=False)

    async def _cmd_diagnose(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) == 1 and args[0] == "database":
            health = await self.storage.healthcheck()
            return session, EngineResult(
                output=f"database backend={health.backend} ok={health.ok} detail={health.detail}",
                prompt=self._prompt(session),
                should_exit=False,
            )

        section_key = self._current_section_key(session)
        if section_key:
            spec = self._section_spec(section_key)
            return session, EngineResult(output=spec.handle_diagnose(), prompt=self._prompt(session), should_exit=False)
        return session, EngineResult(output="diagnose target not supported", prompt=self._prompt(session), should_exit=False)

    async def _cmd_switch(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if not ctx.is_bot_admin:
            raise SectionError("field=switch reason=forbidden hint=bot admin only")
        if len(args) == 1 and args[0] == "root":
            session.scope_type = ScopeType.ROOT
            session.scope_id = 0
            session.current_path = []
            session.candidate_map.clear()
            await self._write_audit_and_system(
                ctx,
                session,
                section="scope",
                action="switch",
                before=None,
                after={"scope": "root"},
                result="ok",
                feature="cli",
                message="switch root",
            )
            return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)
        if len(args) == 2 and args[0] == "guild":
            try:
                guild_id = int(args[1])
            except ValueError as exc:
                raise SectionError("field=guild-id reason=invalid integer hint=use numeric guild id") from exc
            session.scope_type = ScopeType.GUILD
            session.scope_id = guild_id
            session.current_path = []
            session.candidate_map.clear()
            await self._write_audit_and_system(
                ctx,
                session,
                section="scope",
                action="switch",
                before=None,
                after={"scope": f"guild:{guild_id}"},
                result="ok",
                feature="cli",
                message=f"switch guild {guild_id}",
            )
            return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)
        raise SectionError("field=switch reason=invalid args hint=switch root|switch guild <guild-id>")

    async def _handle_crash(
        self,
        ctx: EngineContext,
        session: SessionContext,
        command: str,
        args: list[str],
        exc: Exception,
    ) -> str:
        section = self._counter_section(session)
        trace = traceback.format_exc()
        error_id = self._new_error_id()
        forward_mode = "off"
        forward_status = "not-forwarded"

        context_json = {
            "scope_type": session.scope_type.value,
            "scope_id": session.scope_id,
            "actor_user_id": ctx.actor_user_id,
            "section": section,
            "command": command,
            "args": args,
            "traceback": trace,
            "current_path": list(session.current_path),
        }

        if session.scope_type == ScopeType.GUILD:
            control_plane = await self._load_section_payload(ScopeType.GUILD, session.scope_id, "control-plane")
            send_root = bool(control_plane and control_plane.get("root_connection", {}).get("send_crashlog_root", False))
            if send_root:
                receive_cfg = await self.storage.resolve_receive_config()
                forward_mode = receive_cfg.receive_mode
                forward_status = await self._forward_to_root(error_id, session.scope_id, context_json, receive_cfg)
                await self.storage.trim_crash_logs("root", session.scope_id, receive_cfg.crashlog_max_buffer)

        await self.storage.insert_crash_log(
            scope_type=session.scope_type.value,
            scope_id=session.scope_id,
            actor_user_id=ctx.actor_user_id,
            section=section,
            command=command,
            message=str(exc),
            traceback_text=trace,
            context_json=context_json,
            forward_mode=forward_mode,
            forward_status=forward_status,
            error_id=error_id,
        )
        await self.storage.trim_crash_logs(session.scope_type.value, session.scope_id, 500)
        return error_id

    async def _forward_to_root(
        self,
        source_error_id: str,
        guild_id: int,
        context_json: dict[str, Any],
        receive_cfg: ReceiveConfig,
    ) -> str:
        mode = receive_cfg.receive_mode
        statuses: list[str] = []

        if mode == "off":
            return "drop(mode=off)"

        if mode in {"database", "both"}:
            await self.storage.insert_root_crash_copy(source_error_id, guild_id, context_json)
            statuses.append("database:stored")

        if mode in {"discord", "both"}:
            channel_id = receive_cfg.crashlog_report_channel
            if channel_id is None:
                statuses.append("discord:drop(no-channel)")
            elif self.crash_notifier is None:
                statuses.append("discord:drop(no-notifier)")
            else:
                message = (
                    "[crashlog]\n"
                    f"source_error_id={source_error_id}\n"
                    f"scope=guild:{guild_id}\n"
                    f"section={context_json.get('section')} command={context_json.get('command')}"
                )
                notify_status = await self.crash_notifier(channel_id, message)
                statuses.append(f"discord:{notify_status}")

        if not statuses:
            statuses.append("drop(unsupported-mode)")
        return ",".join(statuses)

    async def _ensure_committed(self, session: SessionContext, section_key: str | None) -> dict[str, Any]:
        if section_key is None:
            return {}

        storage_key = self._storage_section_key(section_key)
        loaded = await self._load_section_payload(session.scope_type, session.scope_id, storage_key)
        if loaded is not None:
            return loaded

        spec = self._section_spec(section_key)
        payload = spec.default_payload()

        if session.scope_type == ScopeType.GUILD:
            root_defaults = await self._load_section_payload(ScopeType.ROOT, 0, "root-defaults")
            if root_defaults:
                section_defaults = root_defaults.get("sections", {}).get(storage_key)
                if isinstance(section_defaults, dict):
                    payload.update(section_defaults)

        payload = spec.validate_payload(payload)
        envelope = ConfigEnvelope(schema_version=spec.schema_version, payload=payload).model_dump(mode="json")
        await self.storage.upsert_config(session.scope_type.value, session.scope_id, storage_key, envelope)
        return payload

    async def _load_section_payload(self, scope_type: ScopeType, scope_id: int, storage_key: str) -> dict[str, Any] | None:
        row = await self.storage.load_config(scope_type.value, scope_id, storage_key)
        if not row:
            return None

        spec_lookup = storage_key
        if storage_key == "guild-log":
            spec_lookup = "guild-log/mod-log"
        if storage_key == "control-plane":
            spec_lookup = "control-plane/root-connection"
        if storage_key == "tenant-connection":
            spec_lookup = "tenant-connection/log"

        spec = self._section_spec(spec_lookup)
        raw = row.data

        if "schema_version" in raw and "payload" in raw:
            schema_version = int(raw["schema_version"])
            payload = dict(raw["payload"])
        else:
            schema_version = 0
            payload = dict(raw)

        migrated_version, migrated_payload = spec.migrate(schema_version, payload)
        validated = spec.validate_payload(migrated_payload)

        if migrated_version != schema_version:
            envelope = ConfigEnvelope(schema_version=spec.schema_version, payload=validated).model_dump(mode="json")
            await self.storage.upsert_config(scope_type.value, scope_id, storage_key, envelope)

        return validated

    async def _check_enforce_guard(self, session: SessionContext, section_key: str, key: str) -> None:
        if session.scope_type != ScopeType.GUILD:
            return
        enforce_payload = await self._load_section_payload(ScopeType.ROOT, 0, "root-enforce")
        if not enforce_payload:
            return

        storage_key = self._storage_section_key(section_key)
        enforced = enforce_payload.get("sections", {}).get(storage_key, {})
        if key in enforced:
            raise SectionError(f"field={key} reason=enforced by root hint=change root-enforce")

    async def _write_audit_and_system(
        self,
        ctx: EngineContext,
        session: SessionContext,
        section: str,
        action: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        result: str,
        feature: str,
        message: str,
    ) -> None:
        await self.storage.insert_audit_log(
            actor_user_id=ctx.actor_user_id,
            scope_type=session.scope_type.value,
            scope_id=session.scope_id,
            section=section,
            action=action,
            before_json=before,
            after_json=after,
            result=result,
        )

        if session.scope_type == ScopeType.GUILD:
            log_type_payload = await self._load_section_payload(ScopeType.GUILD, session.scope_id, "log-type")
            levels = log_type_payload.get("levels", {}) if log_type_payload else {}
            severity = resolve_severity(levels, feature)
        else:
            severity = "info"

        await self.storage.insert_system_log(
            actor_user_id=ctx.actor_user_id,
            scope_id=session.scope_id,
            feature=feature,
            severity=severity,
            message=message,
            detail_json={"result": result},
        )

    def _record_counter(self, scope_id: int, section: str, command: str, ok: bool, error_id: str | None = None) -> None:
        key = (scope_id, section, command)
        current = self.counters.get(key, CounterEntry())
        if ok:
            current.ok_count += 1
        else:
            current.error_count += 1
            current.last_error_at = datetime.now(timezone.utc).isoformat()
            current.last_error_id = error_id
        self.counters[key] = current

    def _format_counters(self, scope_id: int) -> str:
        rows: list[str] = []
        for (counter_scope_id, section, command), value in sorted(self.counters.items(), key=lambda item: (item[0][1], item[0][2])):
            if counter_scope_id != scope_id:
                continue
            rows.append(
                "section={0} command={1} ok={2} error={3} last_error_id={4}".format(
                    section,
                    command,
                    value.ok_count,
                    value.error_count,
                    value.last_error_id or "-",
                )
            )
        if not rows:
            return "counters: (empty)"
        return "\n".join(rows)

    def _counter_section(self, session: SessionContext) -> str:
        return self._current_section_key(session) or "global"

    def _current_section_key(self, session: SessionContext) -> str | None:
        if not session.current_path:
            return None
        if session.current_path == ["guild-log"]:
            return None
        if session.current_path == ["control-plane"]:
            return None
        if session.current_path == ["tenant-connection"]:
            return None
        if session.current_path[0] == "guild-log" and len(session.current_path) == 2:
            return f"guild-log/{session.current_path[1]}"
        if session.current_path[0] == "control-plane" and len(session.current_path) == 2:
            return f"control-plane/{session.current_path[1]}"
        if session.current_path[0] == "tenant-connection" and len(session.current_path) == 2:
            return f"tenant-connection/{session.current_path[1]}"
        return session.current_path[0]

    def _storage_section_key(self, section_key: str) -> str:
        if section_key.startswith("guild-log/"):
            return "guild-log"
        if section_key.startswith("control-plane/"):
            return "control-plane"
        if section_key.startswith("tenant-connection/"):
            return "tenant-connection"
        return section_key

    def _section_spec(self, section_key: str) -> SectionSpec:
        spec = self.sections.get(section_key)
        if not spec:
            raise SectionError(f"field=section reason=unknown section hint={section_key}")
        return spec

    def _prompt(self, session: SessionContext) -> str:
        if session.scope_type == ScopeType.ROOT:
            scope = "root"
        else:
            scope = f"guild:{session.scope_id}"

        if not session.current_path:
            return f"stella({scope})>"

        path = "/".join(session.current_path)
        return f"stella({scope}/{path})#"

    def _new_error_id(self) -> str:
        now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        return f"CR-{now}-{suffix}"
