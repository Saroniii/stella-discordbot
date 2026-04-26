from __future__ import annotations

import traceback
import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils.cli.logs import resolve_severity
from utils.cli.parser import ParseError, parse_line
from utils.cli.sections import build_section_registry
from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.formatter import CliNode, build_sections_tree, render_cli_tree
from utils.cli.types import ConfigEnvelope, EngineContext, EngineResult, ScopeType, SessionContext
from utils.guild_log_cache import guild_message_cache
from utils.tick import TickMeter
from utils.timezone_resolver import resolve_display_timezone_with_meta
from utils.storage import ReceiveConfig, Storage

logger = logging.getLogger(__name__)


HELP_TEXT = """commands:
  help [cmd]
  where | top | quit
  enter <section|?|prefix?> | leave | select <id>|?
  insert <before-id> | move <id> before|after <target-id> | move <id> top|bottom
  set <key> <value...> | set ? | set <prefix>? | set <key> ? | unset <key> | show [now-config [backup]|deploy-config|diff-config [diff-only]] | deploy | discard
  get counters all | get tick status | get guild-log message cache status | get level-table [now-config] | get level me|user <id>|ranking [limit] | get chat-group ...
  get utils webhook
  execute utils create-webhook channel <channel-id> tag "<tag>" | execute utils create-webhook auto-context tag "<tag>" | execute utils delete-webhook <id>
  execute chat-group ...
  execute console thread unused remove <channel-id>
  execute cli to-file start [no-message-response] | execute cli to-file stop
  execute config rebind root-diff|full all-guilds|guild <guild-id> | execute config deploy all-guilds|guild <guild-id>
  execute system restart [keep-active-cli]
  get log audit [limit] | get log system [limit] | get log crash [limit|error_id]
  diagnose database | diagnose config level-policy reorder | diagnose config validate [now-config|deploy-config] | diagnose level-table rebuild
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
    "insert",
    "move",
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
        tick_meter: TickMeter | None = None,
        utils_executor: Callable[[EngineContext, SessionContext, list[str]], Awaitable[str]] | None = None,
        set_validator: Callable[[EngineContext, SessionContext, str, str, list[str]], Awaitable[str | None]] | None = None,
    ) -> None:
        self.storage = storage
        self.sections = build_section_registry()
        self.section_children = self._build_section_children()
        self.storage_spec_lookup = self._build_storage_spec_lookup()
        self.crash_notifier = crash_notifier
        self.utils_executor = utils_executor
        self.set_validator = set_validator
        self.tick_meter = tick_meter or TickMeter(storage)
        self.counters: dict[tuple[int, str, str], CounterEntry] = {}
        self._tz_warning_minute_cache: set[tuple[int, str, str]] = set()

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
            if session.scope_type == ScopeType.GUILD:
                await self.tick_meter.consume(session.scope_id, "command.parse", amount=1, stoppable=False)
            self._record_counter(session.scope_id, "global", "parse", ok=False)
            result = EngineResult(output=f"parse error: {parsed.reason}", prompt=self._prompt(session), should_exit=False)
            return session, await self._maybe_append_next_candidates(ctx, session, result, command="parse")

        command = parsed.name
        args = parsed.args
        if session.scope_type == ScopeType.GUILD:
            await self.tick_meter.consume(session.scope_id, f"command.{command}", amount=1, stoppable=False)

        handlers = {
            "?": self._cmd_question,
            "help": self._cmd_help,
            "where": self._cmd_where,
            "top": self._cmd_top,
            "quit": self._cmd_quit,
            "enter": self._cmd_enter,
            "leave": self._cmd_leave,
            "select": self._cmd_select,
            "insert": self._cmd_insert,
            "move": self._cmd_move,
            "set": self._cmd_set,
            "unset": self._cmd_unset,
            "show": self._cmd_show,
            "deploy": self._cmd_deploy,
            "discard": self._cmd_discard,
            "get": self._cmd_get,
            "diagnose": self._cmd_diagnose,
            "switch": self._cmd_switch,
            "execute": self._cmd_execute,
        }

        handler = handlers.get(command)
        if not handler:
            self._record_counter(session.scope_id, self._counter_section(session), command, ok=False)
            result = EngineResult(output=f"unknown command: {command}", prompt=self._prompt(session), should_exit=False)
            await self._write_command_audit(ctx, session, line, command, args, "error", result.output)
            return session, await self._maybe_append_next_candidates(ctx, session, result, command=command)

        completion_result = await self._handle_completion(ctx, session, command, args)
        if completion_result is not None:
            self._record_counter(session.scope_id, self._counter_section(session), command, ok=True)
            await self._write_command_audit(ctx, session, line, command, args, "ok", completion_result.output)
            return session, await self._maybe_append_next_candidates(ctx, session, completion_result, command=command)

        try:
            updated_session, result = await handler(ctx, session, args)
            if command in TRACKED_COMMANDS:
                self._record_counter(updated_session.scope_id, self._counter_section(updated_session), command, ok=True)
            await self._write_command_audit(ctx, updated_session, line, command, args, "ok", result.output)
            return updated_session, await self._maybe_append_next_candidates(ctx, updated_session, result, command=command)
        except SectionError as exc:
            if command in TRACKED_COMMANDS:
                self._record_counter(session.scope_id, self._counter_section(session), command, ok=False)
            await self._write_command_audit(ctx, session, line, command, args, "error", str(exc))
            result = EngineResult(output=str(exc), prompt=self._prompt(session), should_exit=False)
            return session, await self._maybe_append_next_candidates(ctx, session, result, command=command)
        except Exception as exc:
            error_id = await self._handle_crash(ctx, session, command, args, exc)
            if command in TRACKED_COMMANDS:
                self._record_counter(session.scope_id, self._counter_section(session), command, ok=False, error_id=error_id)
            await self._write_command_audit(ctx, session, line, command, args, "fatal", f"error_id={error_id}")
            result = EngineResult(output=f"fatal error: error_id={error_id}", prompt=self._prompt(session), should_exit=False)
            return session, await self._maybe_append_next_candidates(ctx, session, result, command=command)

    async def _cmd_help(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        return session, EngineResult(output=HELP_TEXT, prompt=self._prompt(session), should_exit=False)

    async def _cmd_question(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if args:
            raise SectionError("field=? reason=invalid args hint=use ?")
        candidates = self._next_candidates(ctx, session)
        return session, EngineResult(
            output=self._format_candidates("candidates:", candidates),
            prompt=self._prompt(session),
            should_exit=False,
        )

    async def _cmd_where(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        path = "/".join(session.current_path) if session.current_path else "(top)"
        output = f"scope={session.scope_type.value}:{session.scope_id} path={path}"
        return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

    async def _cmd_top(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        session.current_path = []
        session.selected_object = None
        session.selected_map.clear()
        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_quit(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        return session, EngineResult(output="session closed", prompt=self._prompt(session), should_exit=True)

    async def _cmd_enter(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) != 1:
            raise SectionError("field=enter reason=invalid args hint=enter <section>")

        target = args[0].lower()
        current_parent = "/".join(session.current_path) if session.current_path else None
        parent_key = current_parent or ""
        known_children = self.section_children.get(parent_key, set())
        if target not in known_children:
            raise SectionError("field=section reason=unknown section hint=use enter ?")

        next_path = list(session.current_path) + [target] if current_parent else [target]
        next_key = "/".join(next_path)

        if not self._path_allowed(next_key, session.scope_type, ctx.is_bot_admin):
            if next_key.startswith("tenant-connection"):
                raise SectionError("field=section reason=forbidden hint=tenant-connection is root-only")
            if next_key.startswith("chat-group-global"):
                raise SectionError("field=section reason=forbidden hint=chat-group-global is root-only")
            if next_key.startswith("control-plane"):
                raise SectionError("field=section reason=forbidden hint=control-plane is guild-only")
            if (
                next_key in {"root-defaults", "root-enforce", "root-enforce-override"}
                or next_key.startswith("root-defaults/")
                or next_key.startswith("root-enforce/")
                or next_key.startswith("root-enforce-override/")
            ):
                raise SectionError("field=section reason=forbidden hint=root sections require root scope and bot admin")
            raise SectionError("field=section reason=forbidden hint=scope permission denied")

        session.current_path = next_path
        if self._is_leaf_section(next_key):
            await self._ensure_config_state(session, next_key)
        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_leave(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if session.current_path:
            session.current_path = session.current_path[:-1]
        session.selected_object = None
        session.selected_map.clear()
        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_select(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) != 1:
            raise SectionError("field=select reason=invalid args hint=select <id>")
        section_key = self._current_section_key(session)
        if not section_key:
            raise SectionError("field=select reason=no active section hint=enter <section>")
        resolved_section_key, resolved_spec = self._resolve_select_spec(section_key, self._section_spec(section_key))
        storage_key = self._storage_section_key(resolved_section_key)
        running_config, _startup_config = await self._ensure_config_state(session, resolved_section_key)
        if resolved_section_key == "sticky-message/channels":
            item_selected = session.selected_map.get("sticky-message")
            if item_selected is None:
                raise SectionError("field=select reason=missing target hint=select <id> in sticky-message")
            selected, updated_payload = resolved_spec.select_target_with_payload_for_item(
                running_config, int(item_selected), args[0]
            )
        elif resolved_section_key == "sticky-message/embed/fields":
            item_selected = session.selected_map.get("sticky-message")
            if item_selected is None:
                raise SectionError("field=select reason=missing target hint=select <id> in sticky-message")
            selected, updated_payload = resolved_spec.select_target_with_payload_for_item(
                running_config, int(item_selected), args[0]
            )
        elif resolved_section_key == "chat-group/member-guilds":
            item_selected = session.selected_map.get("chat-group")
            if item_selected is None:
                raise SectionError("field=select reason=missing target hint=select <id> in chat-group")
            selected, updated_payload = resolved_spec.select_target_with_payload_for_item(
                running_config, int(item_selected), args[0]
            )
        else:
            selected, updated_payload = resolved_spec.select_target_with_payload(running_config, args[0])
        if updated_payload is not None:
            session.running_cache[storage_key] = updated_payload
        self._set_selected(session, resolved_section_key, selected)
        return session, EngineResult(output=f"selected {selected}", prompt=self._prompt(session), should_exit=False)

    async def _cmd_insert(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) != 1:
            raise SectionError("field=insert reason=invalid args hint=insert <before-id>")
        section_key, spec, storage_key, running_config, _startup_config = await self._resolve_section_context(
            session,
            required_field="insert",
            required_hint="enter level-gain-policy",
            required_section="level-gain-policy",
        )
        try:
            before_id = int(args[0])
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id") from exc

        if not hasattr(spec, "insert_before"):
            raise SectionError("field=insert reason=unsupported hint=section does not support insert")
        updated = spec.insert_before(running_config, before_id)
        session.running_cache[storage_key] = updated
        await self._write_audit_and_system(
            ctx,
            session,
            section=storage_key,
            action="insert",
            before=running_config,
            after=updated,
            result="ok",
            feature=section_key,
            message=f"insert before {before_id}",
        )
        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_move(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        section_key, spec, storage_key, running_config, _startup_config = await self._resolve_section_context(
            session,
            required_field="move",
            required_hint="enter level-gain-policy",
            required_section="level-gain-policy",
        )
        if len(args) not in {2, 3}:
            raise SectionError("field=move reason=invalid args hint=move <id> before|after <target-id>|top|bottom")
        try:
            rule_id = int(args[0])
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id") from exc

        mode = args[1]
        target_id: int | None = None
        if mode in {"top", "bottom"}:
            if len(args) != 2:
                raise SectionError("field=move reason=invalid args hint=move <id> before|after <target-id>|top|bottom")
        elif mode in {"before", "after"}:
            if len(args) != 3:
                raise SectionError("field=move reason=invalid args hint=move <id> before|after <target-id>|top|bottom")
            try:
                target_id = int(args[2])
            except ValueError as exc:
                raise SectionError("field=id reason=invalid integer hint=use numeric policy id") from exc
        else:
            raise SectionError("field=move reason=invalid args hint=move <id> before|after <target-id>|top|bottom")

        if not hasattr(spec, "move_rule"):
            raise SectionError("field=move reason=unsupported hint=section does not support move")
        updated = spec.move_rule(running_config, rule_id, mode, target_id)
        session.running_cache[storage_key] = updated
        await self._write_audit_and_system(
            ctx,
            session,
            section=storage_key,
            action="move",
            before=running_config,
            after=updated,
            result="ok",
            feature=section_key,
            message="move policy rule",
        )
        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_set(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) < 2:
            raise SectionError("field=set reason=invalid args hint=set <key> <value...>")
        section_key, spec, storage_key, running_config, _startup_config = await self._resolve_section_context(
            session,
            required_field="set",
            required_hint="enter <section>",
        )

        key = args[0]
        values = args[1:]

        await self._check_tick_edit_guard(ctx, session, section_key)
        await self._check_enforce_guard(session, section_key, key)
        if self.set_validator is not None:
            validation_error = await self.set_validator(ctx, session, section_key, key, values)
            if validation_error:
                raise SectionError(validation_error)
        updated = spec.validate_set_with_context(running_config, key, values, self._selected_for_section(session, section_key))
        session.running_cache[storage_key] = updated

        await self._write_audit_and_system(
            ctx,
            session,
            section=storage_key,
            action="set",
            before=running_config,
            after=updated,
            result="ok",
            feature=section_key,
            message=f"set {key}",
        )

        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_unset(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) != 1:
            raise SectionError("field=unset reason=invalid args hint=unset <key>")
        section_key, spec, storage_key, running_config, _startup_config = await self._resolve_section_context(
            session,
            required_field="unset",
            required_hint="enter <section>",
        )

        key = args[0]
        await self._check_tick_edit_guard(ctx, session, section_key)
        await self._check_enforce_guard(session, section_key, key)
        updated = spec.apply_unset_with_context(running_config, key, self._selected_for_section(session, section_key))
        session.running_cache[storage_key] = updated

        await self._write_audit_and_system(
            ctx,
            session,
            section=storage_key,
            action="unset",
            before=running_config,
            after=updated,
            result="ok",
            feature=section_key,
            message=f"unset {key}",
        )

        return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)

    async def _cmd_show(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        mode, diff_only, backup = self._parse_show_mode(args)
        section_key = self._current_section_key(session)
        if section_key is None and session.current_path:
            parent_section = "/".join(session.current_path)
            child_sections = self._ordered_descendant_sections(parent_section)
            if child_sections:
                output = await self._render_grouped_show(
                    session,
                    mode,
                    diff_only=diff_only,
                    backup=backup,
                    parent_section=parent_section,
                    child_sections=child_sections,
                )
                if mode == "diff" and diff_only and not output:
                    output = "# no differences"
                return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)
        if not section_key:
            output = await self._render_global_show(ctx, session, mode, diff_only=diff_only, backup=backup)
            return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

        if section_key == "control-plane":
            output = await self._render_grouped_show(
                session,
                mode,
                diff_only=diff_only,
                backup=backup,
                parent_section="control-plane",
                child_sections=self._grouped_control_plane_children(),
            )
            if mode == "diff" and diff_only and not output:
                output = "# no differences"
            return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

        _section_key, spec, _storage_key, running_config, startup_config = await self._resolve_section_context(
            session,
            required_field="show",
            required_hint="enter <section>",
        )
        if backup and session.scope_type == ScopeType.GUILD:
            enforce_sections = await self._effective_root_enforce_sections_for_guild(session.scope_id)
            if self._enforced_fields_for_logical(section_key, enforce_sections):
                output = "# excluded by root-enforce for backup"
                return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)
            effective_running = dict(running_config)
        else:
            effective_running = await self._effective_now_config(session, section_key, running_config)
        rendered = spec.render_show_with_context(
            effective_running,
            startup_config,
            self._selected_for_section(session, section_key),
        )
        output = self._render_show_by_mode(rendered, mode, diff_only=diff_only)
        if mode == "diff" and diff_only and not output:
            output = "# no differences"
        return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

    async def _cmd_deploy(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        touched_sections = list(session.running_cache.keys())
        if not touched_sections:
            return session, EngineResult(output="nothing to deploy", prompt=self._prompt(session), should_exit=False)

        changed_sections: list[str] = []
        for storage_key in touched_sections:
            spec_key = self.storage_spec_lookup.get(storage_key, storage_key)
            schema_version = self._section_spec(spec_key).schema_version
            running_payload = dict(session.running_cache[storage_key])
            startup_payload = dict(running_payload)
            session.startup_cache[storage_key] = startup_payload
            envelope = ConfigEnvelope(
                schema_version=schema_version,
                payload={"running_payload": running_payload, "startup_payload": startup_payload},
            ).model_dump(mode="json")
            version = await self.storage.upsert_config(session.scope_type.value, session.scope_id, storage_key, envelope)
            await self.storage.insert_audit_log_safe(
                actor_user_id=ctx.actor_user_id,
                scope_type=session.scope_type.value,
                scope_id=session.scope_id,
                section=storage_key,
                action="deploy",
                before_json=None,
                after_json=startup_payload,
                result=f"version={version}",
            )
            changed_sections.append(f"{storage_key}@v{version}")

        if session.scope_type == ScopeType.GUILD:
            _running, startup = await self._ensure_config_state(session, "log-config")
            log_cfg = startup or {}
            audit_max = int(log_cfg.get("audit_log_max_buffer", 10000))
            system_max = int(log_cfg.get("system_log_max_buffer", 10000))
            await self.storage.trim_logs(scope_id=session.scope_id, audit_max=audit_max, system_max=system_max)

        return session, EngineResult(
            output="deployed startup: " + ", ".join(changed_sections),
            prompt=self._prompt(session),
            should_exit=False,
        )

    async def _cmd_discard(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        restored_sections: list[str] = []
        for storage_key, startup_payload in list(session.startup_cache.items()):
            session.running_cache[storage_key] = dict(startup_payload)
            restored_sections.append(storage_key)
        await self._write_audit_and_system(
            ctx,
            session,
            section="all",
            action="discard",
            before=None,
            after=None,
            result="ok",
            feature="cli",
            message="running restored from startup",
        )
        output = "running restored from startup"
        if restored_sections:
            output += ": " + ", ".join(sorted(restored_sections))
        return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

    async def _cmd_get(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if len(args) < 1:
            raise SectionError(
                "field=get reason=invalid args hint=get counters all|get tick status|get guild-log message cache status|get log audit|get level-table|get level"
            )

        if len(args) == 2 and args[0] == "counters" and args[1] == "all":
            return session, EngineResult(output=self._format_counters(session.scope_id), prompt=self._prompt(session), should_exit=False)

        if len(args) == 2 and args[0] == "tick" and args[1] == "status":
            if session.scope_type != ScopeType.GUILD:
                raise SectionError("field=tick reason=forbidden hint=switch guild <guild-id>")
            status = await self.tick_meter.get_status(session.scope_id)
            display_timezone = await self._resolve_display_timezone_name(ctx, session)
            lines = [
                f"tick status guild={session.scope_id} minute={self._format_timestamp_for_display(status['minute'], display_timezone)}",
                f"used={status['used']} limit={status['limit']} usage={status['usage_percent']}% mode={status['mode']}",
                "categories:",
            ]
            categories = status.get("categories", [])
            if categories:
                for row in categories:
                    lines.append(f"category={row['category']} used={row['used']} share={row['share_percent']}%")
            else:
                lines.append("(empty)")

            lines.append("sources:")
            sources = status.get("sources", [])
            if sources:
                for row in sources:
                    lines.append(f"source={row['source']} used={row['used']} share={row['share_percent']}%")
            else:
                lines.append("(empty)")

            lines.append("history:")
            history = status.get("history", [])
            if history:
                for row in history:
                    lines.append(
                        f"minute={self._format_timestamp_for_display(row['minute'], display_timezone)} "
                        f"used={row['used']} limit={row['limit']} usage={row['usage_percent']}%"
                    )
            else:
                lines.append("(empty)")
            return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)

        if len(args) == 4 and args[0] == "guild-log" and args[1] == "message" and args[2] == "cache" and args[3] == "status":
            if session.scope_type != ScopeType.GUILD:
                raise SectionError("field=guild-log reason=forbidden hint=guild scope only")
            running, _startup = await self._ensure_config_state(session, "guild-log/message-log")
            configured_limit = int(running.get("tracking_message_count", 1000))
            tracking_mode = str(running.get("tracking_message_mode", "normal"))
            status = guild_message_cache.status(session.scope_id, configured_limit=configured_limit)
            lines = [
                f"guild-log message cache status guild={session.scope_id}",
                f"mode={tracking_mode}",
                f"entries={status['message_count']} limit={status['limit']} usage={status['usage_percent']}%",
                f"content_bytes={status['content_bytes']} estimated_bytes={status['estimated_bytes']} avg_content_bytes={status['avg_content_bytes']}",
                f"puts={status['puts']} pops={status['pops']} evictions={status['evictions']}",
                f"hits={status['hits']} misses={status['misses']} hit_rate={status['hit_rate']}%",
            ]
            return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)

        if len(args) == 2 and args[0] == "utils" and args[1] == "webhook":
            display_timezone = await self._resolve_display_timezone_name(ctx, session)
            if session.scope_type == ScopeType.ROOT:
                rows = await self.storage.fetch_utility_webhooks(guild_id=None, limit=500)
            else:
                rows = await self.storage.fetch_utility_webhooks(guild_id=session.scope_id, limit=500)
            lines = ["utils webhook list:"]
            if not rows:
                lines.append("(empty)")
            else:
                for row in rows:
                    lines.append(
                        f"id={row.ref_id} guild={row.guild_id} channel={row.channel_id} tag={row.tag} "
                        f"created_at={self._format_timestamp_for_display(row.created_at, display_timezone)}"
                    )
            return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)

        if args[0] == "level-table":
            if session.scope_type != ScopeType.GUILD:
                raise SectionError("field=level-table reason=forbidden hint=guild scope only")
            if len(args) > 2:
                raise SectionError("field=get reason=invalid args hint=get level-table [now-config]")
            if len(args) == 2 and args[1] != "now-config":
                raise SectionError("field=get reason=invalid args hint=get level-table [now-config]")

            if len(args) == 2 and args[1] == "now-config":
                rows = await self._build_level_table_preview(session, use_startup=False)
                return session, EngineResult(output=self._format_level_table_rows(rows, title="level table (now-config):"), prompt=self._prompt(session), should_exit=False)

            rows = await self.storage.fetch_level_table(session.scope_id)
            return session, EngineResult(output=self._format_level_table_rows(rows, title="level table (rebuilt):"), prompt=self._prompt(session), should_exit=False)

        if args[0] == "level":
            if session.scope_type != ScopeType.GUILD:
                raise SectionError("field=level reason=forbidden hint=guild scope only")
            if len(args) < 2:
                raise SectionError("field=get reason=invalid args hint=get level me|user <id>|ranking [limit]")

            if args[1] == "me":
                if len(args) != 2:
                    raise SectionError("field=get reason=invalid args hint=get level me")
                return session, EngineResult(
                    output=await self._format_level_user_snapshot(session.scope_id, ctx.actor_user_id),
                    prompt=self._prompt(session),
                    should_exit=False,
                )

            if args[1] == "user":
                if len(args) != 3:
                    raise SectionError("field=get reason=invalid args hint=get level user <id>")
                try:
                    user_id = int(args[2])
                except ValueError as exc:
                    raise SectionError("field=user-id reason=invalid integer hint=use numeric user id") from exc
                return session, EngineResult(
                    output=await self._format_level_user_snapshot(session.scope_id, user_id),
                    prompt=self._prompt(session),
                    should_exit=False,
                )

            if args[1] == "ranking":
                if len(args) > 3:
                    raise SectionError("field=get reason=invalid args hint=get level ranking [limit]")
                limit = 10
                if len(args) == 3:
                    try:
                        limit = int(args[2])
                    except ValueError as exc:
                        raise SectionError("field=limit reason=invalid integer hint=use numeric limit") from exc
                rows = await self.storage.fetch_level_ranking(session.scope_id, limit)
                lines = [f"level ranking (limit={max(1, min(limit, 50))}):"]
                for index, row in enumerate(rows, start=1):
                    lines.append(f"{index}. user={row.user_id} level={row.level} total_xp={row.total_xp}")
                if len(lines) == 1:
                    lines.append("(empty)")
                return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)

            raise SectionError("field=get reason=invalid args hint=get level me|user <id>|ranking [limit]")

        if len(args) >= 2 and args[0] == "log" and args[1] in {"audit", "system"}:
            if len(args) > 3:
                raise SectionError("field=get reason=invalid args hint=get log audit|system [limit]")
            if len(args) == 3:
                try:
                    limit = int(args[2])
                except ValueError as exc:
                    raise SectionError("field=limit reason=invalid integer hint=use numeric limit") from exc
            else:
                limit = 50
            rows = await self.storage.fetch_logs_safe(args[1], session.scope_id, limit)
            display_timezone = await self._resolve_display_timezone_name(ctx, session)
            lines = [f"{args[1]} logs (limit={limit}):"]
            for row in rows:
                result_summary = self._summarize_log_value(row.result)
                lines.append(
                    f"[{row.log_id}] at={self._format_timestamp_for_display(row.at, display_timezone)} "
                    f"actor={row.actor_user_id} section={row.section} action={row.action} result={result_summary}"
                )
            if len(lines) == 1:
                lines.append("(empty)")
            return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)

        if len(args) >= 2 and args[0] == "log" and args[1] == "crash":
            scope_type = session.scope_type.value if ctx.is_bot_admin else "guild"
            display_timezone = await self._resolve_display_timezone_name(ctx, session)

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
                row = await self.storage.fetch_crash_log_by_error_id_safe(
                    scope_type=scope_type,
                    scope_id=session.scope_id,
                    error_id=error_id,
                )
                if row is None:
                    return session, EngineResult(
                        output=f"crash log not found: error_id={error_id}",
                        prompt=self._prompt(session),
                        should_exit=False,
                    )
                context = row.context_json if isinstance(row.context_json, dict) else {}
                detail = [
                    f"error_id={row.error_id}",
                    f"at={self._format_timestamp_for_display(row.at, display_timezone)}",
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

            rows = await self.storage.fetch_crash_logs_safe(scope_type=scope_type, scope_id=session.scope_id, limit=limit)
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
                    f"[{row.error_id}] at={self._format_timestamp_for_display(row.at, display_timezone)} scope={scope_text} actor={actor} "
                    f"section={row.section} command={row.command} args={cmd_args} "
                    f"path={path_text} message={row.message} forward={row.forward_status}"
                )
            if len(lines) == 1:
                lines.append("(empty)")
            return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)

        if args[0] == "chat-group":
            if len(args) == 2 and args[1] == "list":
                if session.scope_type == ScopeType.ROOT:
                    lines = ["chat-group list (root): use guild scope to view joined groups"]
                    return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)
                rows = await self.storage.list_chat_groups_for_guild(session.scope_id)
                lines = ["chat-group list:"]
                for row in rows:
                    lines.append(
                        f"group-id={row.group_id} name={row.name} mode={row.mode} status={row.status} leader-guild={row.leader_guild_id}"
                    )
                if len(lines) == 1:
                    lines.append("(empty)")
                return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)
            if len(args) >= 3:
                group_id = args[1]
                sub = args[2]
                group = await self.storage.get_chat_group(group_id)
                if group is None:
                    raise SectionError("field=group-id reason=not found hint=use get chat-group list")
                if sub == "status":
                    memberships = await self.storage.list_chat_group_memberships(group_id)
                    connections = await self.storage.list_chat_group_connections(group_id)
                    lines = [
                        f"chat-group status group-id={group.group_id}",
                        f"name={group.name} mode={group.mode} status={group.status} join-need-apply={'enable' if group.join_need_apply else 'disable'}",
                        f"leader-guild={group.leader_guild_id} rate-limit={group.rate_limit} overlimit-mode={group.overlimit_mode} slowmode-sec={group.slowmode_sec}",
                        "member-guilds:",
                    ]
                    for row in memberships:
                        conn = next((item for item in connections if item.guild_id == row.guild_id), None)
                        channel_text = str(conn.channel_id) if conn else "-"
                        lines.append(f"guild={row.guild_id} role={row.role} status={row.status} channel={channel_text}")
                    if len(memberships) == 0:
                        lines.append("(empty)")
                    return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)
                if sub == "apply-list":
                    rows = await self.storage.list_chat_group_applications(group_id, status="pending")
                    lines = [f"chat-group apply-list group-id={group_id}"]
                    for row in rows:
                        lines.append(
                            f"apply-id={row.apply_id} guild={row.guild_id} channel={row.channel_id} status={row.status} requested-at={row.requested_at}"
                        )
                    if len(lines) == 1:
                        lines.append("(empty)")
                    return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)
                if sub == "auth-key" and len(args) == 4 and args[3] == "list":
                    rows = await self.storage.list_chat_group_auth_keys(group_id)
                    lines = [f"chat-group auth-key list group-id={group_id}"]
                    for row in rows:
                        guild_scope = str(row.guild_id) if row.guild_id is not None else "any"
                        lines.append(
                            f"id={row.id} scope-guild={guild_scope} status={row.status} key-preview={row.key_preview} created-at={row.created_at}"
                        )
                    if len(lines) == 1:
                        lines.append("(empty)")
                    return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)
                if sub == "message" and len(args) == 4:
                    try:
                        message_id = int(args[3])
                    except ValueError as exc:
                        raise SectionError("field=message-id reason=invalid integer hint=use numeric id") from exc
                    row = await self.storage.get_chat_group_message(message_id)
                    if row is None or row.group_id != group_id:
                        raise SectionError("field=message-id reason=not found hint=check group/message id")
                    deliveries = await self.storage.list_chat_group_deliveries(message_id)
                    lines = [
                        f"chat-group message group-id={group_id} message-id={message_id}",
                        f"source-guild={row.source_guild_id} source-channel={row.source_channel_id} source-message={row.source_message_id}",
                        f"author={row.author_user_id} name={row.author_name}",
                        f"deleted={'enable' if row.deleted else 'disable'}",
                        f"content={row.content}",
                        "deliveries:",
                    ]
                    for item in deliveries:
                        lines.append(
                            f"guild={item['target_guild_id']} channel={item['target_channel_id']} target-message={item['target_message_id']} status={item['status']} error={item['error'] or '-'}"
                        )
                    if len(deliveries) == 0:
                        lines.append("(empty)")
                    return session, EngineResult(output="\n".join(lines), prompt=self._prompt(session), should_exit=False)
            raise SectionError(
                "field=get reason=invalid args hint=get chat-group list|get chat-group <group-id> status|apply-list|auth-key list|message <message-id>"
            )

        if len(args) < 2:
            raise SectionError(
                "field=get reason=invalid args hint=get counters all|get tick status|get guild-log message cache status|get log audit|get level-table|get level"
            )

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
        if len(args) == 3 and args[0] == "config" and args[1] == "level-policy" and args[2] == "reorder":
            section_key = "level-gain-policy"
            spec = self._section_spec(section_key)
            if not hasattr(spec, "reorder_ids"):
                raise SectionError("field=diagnose reason=unsupported hint=level policy reorder unavailable")
            storage_key = self._storage_section_key(section_key)
            running_config, _startup_config = await self._ensure_config_state(session, section_key)
            updated = spec.reorder_ids(running_config)
            session.running_cache[storage_key] = updated
            await self._write_audit_and_system(
                ctx,
                session,
                section=storage_key,
                action="reorder",
                before=running_config,
                after=updated,
                result="ok",
                feature=section_key,
                message="diagnose config level-policy reorder",
            )
            return session, EngineResult(output="ok", prompt=self._prompt(session), should_exit=False)
        if len(args) == 2 and args[0] == "level-table" and args[1] == "rebuild":
            if session.scope_type != ScopeType.GUILD:
                raise SectionError("field=level-table reason=forbidden hint=guild scope only")
            rows = await self._build_level_table_preview(session, use_startup=True)
            await self.storage.replace_level_table(session.scope_id, rows)
            await self._write_audit_and_system(
                ctx,
                session,
                section="level-table",
                action="rebuild",
                before=None,
                after={"rows": len(rows)},
                result="ok",
                feature="level-common",
                message="diagnose level-table rebuild",
            )
            return session, EngineResult(output=f"ok rebuilt rows={len(rows)}", prompt=self._prompt(session), should_exit=False)
        if len(args) in {2, 3} and args[0] == "config" and args[1] == "validate":
            if session.scope_type != ScopeType.GUILD:
                raise SectionError("field=diagnose reason=forbidden hint=guild scope only")
            target = "now-config"
            if len(args) == 3:
                target = args[2]
            if target not in {"now-config", "deploy-config"}:
                raise SectionError("field=diagnose reason=invalid args hint=diagnose config validate [now-config|deploy-config]")
            output = await self._diagnose_config_validate(ctx, session, target)
            return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

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
            session.selected_object = None
            session.selected_map.clear()
            session.running_cache.clear()
            session.startup_cache.clear()
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
            session.selected_object = None
            session.selected_map.clear()
            session.running_cache.clear()
            session.startup_cache.clear()
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

    async def _cmd_execute(self, ctx: EngineContext, session: SessionContext, args: list[str]) -> tuple[SessionContext, EngineResult]:
        if not args or args[0] not in {"utils", "console", "cli", "config", "system", "chat-group"}:
            raise SectionError(
                "field=execute reason=invalid args hint=execute utils ... | execute console ... | execute cli to-file start [no-message-response]|stop | execute config ... | execute system ... | execute chat-group ..."
            )
        if self.utils_executor is None:
            raise SectionError("field=execute reason=unsupported hint=executor is not configured")
        output = await self.utils_executor(ctx, session, args)
        return session, EngineResult(output=output, prompt=self._prompt(session), should_exit=False)

    async def _diagnose_config_validate(self, ctx: EngineContext, session: SessionContext, target: str) -> str:
        errors: list[str] = []
        warnings: list[str] = []
        checked = 0

        mgmt_running, mgmt_startup = await self._ensure_config_state(session, "management-module")
        mgmt_payload = mgmt_running if target == "now-config" else mgmt_startup
        module_enabled = {
            "welcome": bool(mgmt_payload.get("welcome", True)),
            "level": bool(mgmt_payload.get("level", True)),
            "sticky-message": bool(mgmt_payload.get("sticky_message", False)),
            "auto-reaction": bool(mgmt_payload.get("auto_reaction", False)),
        }

        section_module_map = {
            "welcome": "welcome",
            "level-common": "level",
            "level-method-message": "level",
            "level-method-reaction": "level",
            "level-method-voice": "level",
            "level-shared": "level",
            "level-segment-table": "level",
            "level-static-table": "level",
            "level-gain-policy": "level",
            "sticky-message": "sticky-message",
            "sticky-message/channels": "sticky-message",
            "sticky-message/channels/webhook": "sticky-message",
            "sticky-message/embed": "sticky-message",
            "sticky-message/embed/fields": "sticky-message",
            "auto-reaction": "auto-reaction",
        }

        for section_key in sorted(self.sections.keys()):
            if self._is_root_only_section(section_key):
                continue
            checked += 1
            spec = self._section_spec(section_key)
            storage_key = self._storage_section_key(section_key)
            running, startup = await self._ensure_config_state(session, section_key)
            payload = dict(running if target == "now-config" else startup)
            try:
                spec.validate_payload(payload)
            except SectionError as exc:
                errors.append(f"section={section_key} {exc}")
                continue
            except Exception as exc:
                errors.append(f"section={section_key} field=payload reason={exc.__class__.__name__} hint=unexpected validate error")
                continue

            module_name = section_module_map.get(section_key)
            if module_name and not module_enabled.get(module_name, True):
                default_payload = spec.default_payload()
                if payload != default_payload:
                    warnings.append(
                        f"section={section_key} warn=module-disabled module={module_name} hint=management-module.{module_name.replace('-', '_')} is disable"
                    )

            warnings.extend(await self._validate_config_references(ctx, session, section_key, payload))

        summary = f"diagnose config validate target={target} checked={checked} ok={checked - len(errors)} warn={len(warnings)} error={len(errors)}"
        lines = [summary]
        if warnings:
            lines.append("warnings:")
            lines.extend(warnings[:200])
        if errors:
            lines.append("errors:")
            lines.extend(errors[:200])
        return "\n".join(lines)

    async def _validate_config_references(
        self, ctx: EngineContext, session: SessionContext, section_key: str, payload: dict[str, Any]
    ) -> list[str]:
        if ctx.guild is None:
            return []
        warnings: list[str] = []
        flat_values = self._flatten_payload(payload)
        get_channel = getattr(ctx.guild, "get_channel", None)
        get_role = getattr(ctx.guild, "get_role", None)
        for path, value in flat_values:
            key_name = path.split(".")[-1]
            normalized = key_name.lower()
            if normalized in {"channel", "channel_id"}:
                if isinstance(value, int) and callable(get_channel) and get_channel(value) is None:
                    warnings.append(f"section={section_key} warn=missing-channel field={path} value={value}")
            if normalized in {"channels", "channel_ids"} and isinstance(value, list):
                for channel_id in value:
                    if isinstance(channel_id, int) and callable(get_channel) and get_channel(channel_id) is None:
                        warnings.append(f"section={section_key} warn=missing-channel field={path} value={channel_id}")
            if normalized in {"role", "role_id"}:
                if isinstance(value, int) and callable(get_role) and get_role(value) is None:
                    warnings.append(f"section={section_key} warn=missing-role field={path} value={value}")
            if normalized in {"roles", "role_ids", "join_roles"} and isinstance(value, list):
                for role_id in value:
                    if isinstance(role_id, int) and callable(get_role) and get_role(role_id) is None:
                        warnings.append(f"section={section_key} warn=missing-role field={path} value={role_id}")
            if normalized == "webhook" and isinstance(value, str) and value.startswith("wh-"):
                row = await self.storage.get_utility_webhook(value)
                if row is None:
                    warnings.append(f"section={section_key} warn=missing-webhook field={path} value={value}")
                elif session.scope_type == ScopeType.GUILD and row.guild_id != session.scope_id:
                    warnings.append(f"section={section_key} warn=webhook-scope-mismatch field={path} value={value}")
        return warnings

    def _flatten_payload(self, payload: Any, prefix: str = "") -> list[tuple[str, Any]]:
        rows: list[tuple[str, Any]] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                child = f"{prefix}.{key}" if prefix else str(key)
                rows.extend(self._flatten_payload(value, child))
            return rows
        if isinstance(payload, list):
            rows.append((prefix, payload))
            return rows
        rows.append((prefix, payload))
        return rows

    def _is_root_only_section(self, section_key: str) -> bool:
        return (
            section_key in {"root-defaults", "root-enforce", "root-enforce-override", "tenant-connection", "chat-group-global"}
            or section_key.startswith("root-defaults/")
            or section_key.startswith("root-enforce/")
            or section_key.startswith("root-enforce-override/")
            or section_key.startswith("tenant-connection/")
            or section_key.startswith("chat-group-global/")
        )

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
            try:
                control_plane_state = await self._load_section_payload(ScopeType.GUILD, session.scope_id, "control-plane")
                control_plane = control_plane_state[0] if control_plane_state else None
                send_root = bool(control_plane and control_plane.get("root_connection", {}).get("send_crashlog_root", False))
                if send_root:
                    receive_cfg = await self.storage.resolve_receive_config()
                    forward_mode = receive_cfg.receive_mode
                    forward_status = await self._forward_to_root(error_id, session.scope_id, context_json, receive_cfg)
                    try:
                        await self.storage.trim_crash_logs("root", session.scope_id, receive_cfg.crashlog_max_buffer)
                    except Exception:
                        self._record_counter(session.scope_id, "resilience", "crash-root-trim", ok=False, error_id=error_id)
                        logger.warning("root crash log trim failed", exc_info=True)
            except Exception:
                logger.warning("crash forward pipeline failed", exc_info=True)
                self._record_counter(session.scope_id, "resilience", "crash-forward-pipeline", ok=False, error_id=error_id)
                forward_status = "forward-failed"

        saved, persisted_error_id = await self.storage.insert_crash_log_safe(
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
        error_id = persisted_error_id
        if not saved:
            self._record_counter(session.scope_id, "resilience", "crash-persist", ok=False, error_id=error_id)
            logger.warning("failed to persist crash log: error_id=%s", error_id)
        try:
            await self.storage.trim_crash_logs(session.scope_type.value, session.scope_id, 500)
        except Exception:
            self._record_counter(session.scope_id, "resilience", "crash-local-trim", ok=False, error_id=error_id)
            logger.warning("crash log trim failed: scope=%s:%s", session.scope_type.value, session.scope_id, exc_info=True)
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
            try:
                await self.storage.insert_root_crash_copy(source_error_id, guild_id, context_json)
                statuses.append("database:stored")
            except Exception:
                self._record_counter(guild_id, "resilience", "crash-root-copy", ok=False, error_id=source_error_id)
                logger.warning("root crash copy failed", exc_info=True)
                statuses.append("database:failed")

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
                try:
                    notify_status = await self.crash_notifier(channel_id, message)
                except Exception:
                    self._record_counter(guild_id, "resilience", "crash-discord-notify", ok=False, error_id=source_error_id)
                    logger.warning("crash notifier failed", exc_info=True)
                    notify_status = "failed(exception)"
                statuses.append(f"discord:{notify_status}")

        if not statuses:
            statuses.append("drop(unsupported-mode)")
        return ",".join(statuses)

    async def _ensure_config_state(self, session: SessionContext, section_key: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        if section_key is None:
            return {}, {}

        storage_key = self._storage_section_key(section_key)
        running_cached = session.running_cache.get(storage_key)
        startup_cached = session.startup_cache.get(storage_key)
        if isinstance(running_cached, dict) and isinstance(startup_cached, dict):
            return running_cached, startup_cached

        state = await self._load_section_payload(session.scope_type, session.scope_id, storage_key)
        spec = self._section_spec(section_key)

        if state is not None:
            running_payload, startup_payload = state
            session.running_cache[storage_key] = running_payload
            session.startup_cache[storage_key] = startup_payload
            return running_payload, startup_payload

        running_payload = spec.default_payload()
        if session.scope_type == ScopeType.GUILD:
            root_defaults_state = await self._load_section_payload(ScopeType.ROOT, 0, "root-defaults")
            if root_defaults_state is not None:
                root_running, _root_startup = root_defaults_state
                default_sections = root_running.get("sections", {})
                if isinstance(default_sections, dict):
                    running_payload = self._apply_root_enforce_overlay(
                        section_key,
                        running_payload,
                        default_sections,
                    )

        running_payload = spec.validate_payload(running_payload)
        startup_payload = dict(running_payload)
        session.running_cache[storage_key] = dict(running_payload)
        session.startup_cache[storage_key] = dict(startup_payload)
        envelope = ConfigEnvelope(
            schema_version=spec.schema_version,
            payload={"running_payload": running_payload, "startup_payload": startup_payload},
        ).model_dump(mode="json")
        await self.storage.upsert_config(session.scope_type.value, session.scope_id, storage_key, envelope)
        return session.running_cache[storage_key], session.startup_cache[storage_key]

    async def _load_section_payload(
        self, scope_type: ScopeType, scope_id: int, storage_key: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        row = await self.storage.load_config(scope_type.value, scope_id, storage_key)
        if not row:
            return None

        spec_lookup = self.storage_spec_lookup.get(storage_key, storage_key)

        spec = self._section_spec(spec_lookup)
        raw = row.data

        schema_version = 0
        payload_obj: dict[str, Any]
        if "schema_version" in raw and "payload" in raw:
            schema_version = int(raw["schema_version"])
            payload_obj = dict(raw["payload"])
        else:
            payload_obj = dict(raw)

        has_running = isinstance(payload_obj.get("running_payload"), dict)
        has_startup = isinstance(payload_obj.get("startup_payload"), dict)
        if has_running and has_startup:
            running_payload = dict(payload_obj["running_payload"])
            startup_payload = dict(payload_obj["startup_payload"])
        elif has_running:
            running_payload = dict(payload_obj["running_payload"])
            startup_payload = dict(running_payload)
            schema_version = schema_version or 0
        elif has_startup:
            startup_payload = dict(payload_obj["startup_payload"])
            running_payload = dict(startup_payload)
            schema_version = schema_version or 0
        else:
            legacy_payload = dict(payload_obj)
            running_payload = dict(legacy_payload)
            startup_payload = dict(legacy_payload)
            schema_version = schema_version or 0

        migrated_running_version, migrated_running = spec.migrate(schema_version, running_payload)
        migrated_startup_version, migrated_startup = spec.migrate(schema_version, startup_payload)
        validated_running = spec.validate_payload(migrated_running)
        validated_startup = spec.validate_payload(migrated_startup)

        needs_migration = (
            "running_payload" not in payload_obj
            or "startup_payload" not in payload_obj
            or migrated_running_version != schema_version
            or migrated_startup_version != schema_version
        )
        if needs_migration:
            envelope = ConfigEnvelope(
                schema_version=spec.schema_version,
                payload={"running_payload": validated_running, "startup_payload": validated_startup},
            ).model_dump(mode="json")
            await self.storage.upsert_config(scope_type.value, scope_id, storage_key, envelope)

        return validated_running, validated_startup

    async def _check_enforce_guard(self, session: SessionContext, section_key: str, key: str) -> None:
        if session.scope_type != ScopeType.GUILD:
            return
        sections = await self._effective_root_enforce_sections_for_guild(session.scope_id)
        if not sections:
            return

        enforced = sections.get(section_key, {})
        if isinstance(enforced, dict) and key in enforced:
            raise SectionError(f"field={key} reason=enforced by root hint=change root-enforce")

        storage_key = self._storage_section_key(section_key)
        storage_enforced = sections.get(storage_key, {})
        if isinstance(storage_enforced, dict) and key in storage_enforced:
            raise SectionError(f"field={key} reason=enforced by root hint=change root-enforce")

    async def _check_tick_edit_guard(self, ctx: EngineContext, session: SessionContext, section_key: str) -> None:
        if section_key != "control-plane/tick":
            return
        if session.scope_type != ScopeType.GUILD:
            raise SectionError("field=tick reason=forbidden hint=guild scope only")
        if not ctx.is_bot_admin:
            raise SectionError("field=tick reason=forbidden hint=bot admin only")

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
        if session.scope_type == ScopeType.GUILD:
            await self.tick_meter.consume(session.scope_id, "log.audit.write", amount=1, stoppable=False)
        await self.storage.insert_audit_log_safe(
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
            log_type_state = await self._load_section_payload(ScopeType.GUILD, session.scope_id, "log-type")
            log_type_payload = log_type_state[0] if log_type_state else None
            levels = log_type_payload.get("levels", {}) if log_type_payload else {}
            severity = resolve_severity(levels, feature)
        else:
            severity = "info"

        await self.storage.insert_system_log_safe(
            actor_user_id=ctx.actor_user_id,
            scope_id=session.scope_id,
            feature=feature,
            severity=severity,
            message=message,
            detail_json={"result": result},
        )
        if session.scope_type == ScopeType.GUILD:
            await self.tick_meter.consume(session.scope_id, "log.system.write", amount=1, stoppable=False)

    async def _write_command_audit(
        self,
        ctx: EngineContext,
        session: SessionContext,
        line: str,
        command: str,
        args: list[str],
        status: str,
        result_text: str,
    ) -> None:
        if session.scope_type == ScopeType.GUILD:
            await self.tick_meter.consume(session.scope_id, "log.audit.write", amount=1, stoppable=False)
        await self.storage.insert_audit_log_safe(
            actor_user_id=ctx.actor_user_id,
            scope_type=session.scope_type.value,
            scope_id=session.scope_id,
            section=self._counter_section(session),
            action=f"command:{command}",
            before_json={"line": line, "args": args},
            after_json=None,
            result=f"{status}:{result_text[:180]}",
        )

    async def _handle_completion(
        self,
        ctx: EngineContext,
        session: SessionContext,
        command: str,
        args: list[str],
    ) -> EngineResult | None:
        if command == "enter" and len(args) == 1 and self._has_question_suffix(args[0]):
            prefix = self._strip_question_suffix(args[0]).lower()
            candidates = [item for item in self._enter_candidates(ctx, session) if item.startswith(prefix)]
            return EngineResult(output=self._format_candidates("candidates:", candidates), prompt=self._prompt(session), should_exit=False)

        if command == "set":
            section_key = self._current_section_key(session)
            if section_key is None:
                return None
            spec = self._section_spec(section_key)

            if len(args) == 1 and self._has_question_suffix(args[0]):
                prefix = self._strip_question_suffix(args[0]).lower()
                set_keys = self._set_key_candidates(ctx, session, section_key, spec)
                candidates = [item for item in set_keys if item.startswith(prefix)]
                return EngineResult(output=self._format_candidates("candidates:", candidates), prompt=self._prompt(session), should_exit=False)

            if len(args) == 2 and args[1] in {"?", "？"}:
                candidates = spec.list_value_candidates(args[0].lower())
                return EngineResult(output=self._format_candidates("candidates:", candidates), prompt=self._prompt(session), should_exit=False)

        if command == "select" and len(args) == 1 and args[0] == "?":
            section_key = self._current_section_key(session)
            if section_key is None:
                return None
            try:
                spec = self._section_spec(section_key)
                resolved_section_key, resolved_spec = self._resolve_select_spec(section_key, spec)
                running_config, _startup_config = await self._ensure_config_state(session, resolved_section_key)
            except SectionError:
                return EngineResult(
                    output="field=select reason=invalid context hint=select not supported in this section",
                    prompt=self._prompt(session),
                    should_exit=False,
                )
            if resolved_section_key in {"sticky-message/channels", "sticky-message/embed/fields"}:
                item_selected = session.selected_map.get("sticky-message")
                if item_selected is None:
                    candidates = []
                else:
                    candidates = resolved_spec.list_select_candidates_for_item(running_config, int(item_selected))
            else:
                candidates = resolved_spec.list_select_candidates(running_config)
            return EngineResult(output=self._format_candidates("candidates:", candidates), prompt=self._prompt(session), should_exit=False)

        return None

    async def _maybe_append_next_candidates(
        self,
        ctx: EngineContext,
        session: SessionContext,
        result: EngineResult,
        command: str,
    ) -> EngineResult:
        if result.should_exit or command == "quit":
            return result
        if session.scope_type != ScopeType.GUILD:
            return result

        console_state = await self._load_section_payload(ScopeType.GUILD, session.scope_id, "console")
        console_payload = console_state[0] if console_state else None
        enabled = bool(console_payload and console_payload.get("always_print_help", False))
        if not enabled:
            return result

        candidates = self._next_candidates(ctx, session)
        suffix = self._format_candidates("next candidates:", candidates)
        if result.output:
            merged = f"{result.output}\n\n{suffix}"
        else:
            merged = suffix
        return EngineResult(output=merged, prompt=result.prompt, should_exit=result.should_exit)

    def _next_candidates(self, ctx: EngineContext, session: SessionContext) -> list[str]:
        section_key = self._current_section_key(session)
        if section_key is not None:
            spec = self._section_spec(section_key)
            keys = self._set_key_candidates(ctx, session, section_key, spec)
            tokens = [f"set {key}" for key in keys]
            if self._section_supports_select(section_key, spec):
                tokens.append("select <id>")
            child_tokens = sorted(self.section_children.get(section_key, set()))
            tokens.extend([f"enter {child}" for child in child_tokens])
            return tokens + ["leave", "top", "quit"]
        return [f"enter {item}" for item in self._enter_candidates(ctx, session)] + ["quit"]

    def _section_supports_select(self, section_key: str, spec: SectionSpec) -> bool:
        try:
            self._resolve_select_spec(section_key, spec)
            return True
        except SectionError:
            return False

    def _resolve_select_spec(self, section_key: str, spec: SectionSpec) -> tuple[str, SectionSpec]:
        if spec.supports_select():
            return section_key, spec
        delegate = spec.select_delegate_section()
        if not delegate:
            raise SectionError("field=select reason=invalid context hint=select not supported in this section")
        delegate_spec = self._section_spec(delegate)
        if not delegate_spec.supports_select():
            raise SectionError("field=select reason=invalid context hint=select not supported in this section")
        return delegate, delegate_spec

    def _set_selected(self, session: SessionContext, section_key: str, selected: str) -> None:
        session.selected_object = selected
        session.selected_map[section_key] = selected

    def _selected_for_section(self, session: SessionContext, section_key: str) -> str | None:
        direct = session.selected_map.get(section_key)
        if section_key == "sticky-message/channels":
            item_selected = session.selected_map.get("sticky-message")
            if item_selected is None:
                return None
            if direct is None:
                return f"{item_selected}:"
            return f"{item_selected}:{direct}"
        if section_key == "sticky-message/channels/webhook":
            item_selected = session.selected_map.get("sticky-message")
            channel_selected = session.selected_map.get("sticky-message/channels")
            if item_selected is None or channel_selected is None:
                return None
            return f"{item_selected}:{channel_selected}"
        if section_key == "sticky-message/embed":
            return session.selected_map.get("sticky-message")
        if section_key == "sticky-message/embed/fields":
            item_selected = session.selected_map.get("sticky-message")
            if item_selected is None:
                return None
            if direct is None:
                return f"{item_selected}:"
            return f"{item_selected}:{direct}"
        if section_key == "chat-group/connection":
            return session.selected_map.get("chat-group")
        if section_key == "chat-group/member-guilds":
            item_selected = session.selected_map.get("chat-group")
            if item_selected is None:
                return None
            if direct is None:
                return f"{item_selected}:"
            return f"{item_selected}:{direct}"
        if direct is not None:
            return direct
        return session.selected_object

    def _set_key_candidates(self, ctx: EngineContext, session: SessionContext, section_key: str, spec: SectionSpec) -> list[str]:
        keys = list(spec.list_set_keys())
        if section_key != "log-type":
            return keys

        hidden_aliases = {"mod-log", "message-log", "member-log", "control-plane", "tenant-connection"}
        filtered: list[str] = []
        for key in keys:
            if "/" in key:
                continue
            if key in hidden_aliases:
                continue
            if key in self.sections:
                if not self._path_allowed(key, session.scope_type, ctx.is_bot_admin):
                    continue
            filtered.append(key)
        return filtered

    def _enter_candidates(self, ctx: EngineContext, session: SessionContext) -> list[str]:
        parent = "/".join(session.current_path) if session.current_path else ""
        children = sorted(self.section_children.get(parent, set()))
        if not children:
            return []
        return [
            child
            for child in children
            if self._path_allowed("/".join([part for part in [parent, child] if part]), session.scope_type, ctx.is_bot_admin)
        ]

    def _format_candidates(self, title: str, candidates: list[str]) -> str:
        if not candidates:
            return f"{title}\n(none)"
        return "\n".join([title, *candidates])

    def _has_question_suffix(self, value: str) -> bool:
        return value.endswith("?") or value.endswith("？")

    def _strip_question_suffix(self, value: str) -> str:
        if value.endswith("?") or value.endswith("？"):
            return value[:-1]
        return value

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

    async def _render_global_show(
        self,
        ctx: EngineContext,
        session: SessionContext,
        mode: str,
        *,
        diff_only: bool = False,
        backup: bool = False,
    ) -> str:
        if session.scope_type != ScopeType.GUILD:
            return "show(global) is guild-only"

        title = {
            "now": "now-config:",
            "deploy": "deploy-config:",
            "diff": "diff-config:",
        }[mode]
        lines: list[str] = [title]
        enforce_sections = await self._effective_root_enforce_sections_for_guild(session.scope_id)
        if mode == "now" and not backup:
            lines.append(await self._render_root_enforce_block_for_guild(session))
            lines.append("")
        rendered_guild_log = False
        rendered_control_plane = False
        rendered_sticky_message = False
        rendered_chat_group = False
        for logical_section in self._global_show_sections_for_guild():
            if logical_section.startswith("chat-group"):
                if rendered_chat_group:
                    continue
                lines.append(
                    await self._render_grouped_show(
                        session,
                        mode,
                        diff_only=diff_only,
                        backup=backup,
                        parent_section="chat-group",
                        child_sections=self._grouped_chat_group_sections(),
                    )
                )
                if lines[-1] == "":
                    lines.pop()
                else:
                    lines.append("")
                rendered_chat_group = True
                continue
            if logical_section.startswith("sticky-message"):
                if rendered_sticky_message:
                    continue
                lines.append(
                    await self._render_grouped_show(
                        session,
                        mode,
                        diff_only=diff_only,
                        backup=backup,
                        parent_section="sticky-message",
                        child_sections=self._grouped_sticky_message_sections(),
                    )
                )
                if lines[-1] == "":
                    lines.pop()
                else:
                    lines.append("")
                rendered_sticky_message = True
                continue
            if logical_section.startswith("guild-log/"):
                if rendered_guild_log:
                    continue
                lines.append(
                    await self._render_grouped_show(
                        session,
                        mode,
                        diff_only=diff_only,
                        backup=backup,
                        parent_section="guild-log",
                        child_sections=self._grouped_guild_log_children(),
                    )
                )
                if lines[-1] == "":
                    lines.pop()
                else:
                    lines.append("")
                rendered_guild_log = True
                continue
            if logical_section == "control-plane" or logical_section.startswith("control-plane/"):
                if rendered_control_plane:
                    continue
                lines.append(
                    await self._render_grouped_show(
                        session,
                        mode,
                        diff_only=diff_only,
                        backup=backup,
                        parent_section="control-plane",
                        child_sections=self._grouped_control_plane_children(),
                    )
                )
                if lines[-1] == "":
                    lines.pop()
                else:
                    lines.append("")
                rendered_control_plane = True
                continue
            await self._ensure_config_state(session, logical_section)
            now_config = self._now_config_for_section(session, logical_section)
            deploy_config = self._deploy_config_for_section(session, logical_section)
            if backup and mode == "now" and self._enforced_fields_for_logical(logical_section, enforce_sections):
                continue
            spec = self._section_spec(logical_section)
            effective_now = dict(now_config or {}) if backup else await self._effective_now_config(session, logical_section, now_config or {})
            rendered = spec.render_show_with_context(
                effective_now,
                deploy_config,
                self._selected_for_section(session, logical_section),
            )
            block = self._render_show_by_mode(rendered, mode, diff_only=diff_only)
            if mode == "diff" and diff_only and not block:
                continue
            lines.append(block)
            lines.append("")
        output = "\n".join(lines).rstrip()
        if mode == "diff" and diff_only and output == "diff-config:":
            return "diff-config:\n# no differences"
        return output

    async def _render_grouped_show(
        self,
        session: SessionContext,
        mode: str,
        *,
        diff_only: bool = False,
        backup: bool = False,
        parent_section: str,
        child_sections: list[str],
    ) -> str:
        enforce_sections = await self._effective_root_enforce_sections_for_guild(session.scope_id) if (backup and session.scope_type == ScopeType.GUILD) else {}
        if mode == "diff":
            now_root = CliNode(kind="enter", text=f"enter {parent_section}")
            deploy_root = CliNode(kind="enter", text=f"enter {parent_section}")
            changed = False
            for child in child_sections:
                await self._ensure_config_state(session, child)
                now_config = self._now_config_for_section(session, child)
                deploy_config = self._deploy_config_for_section(session, child)
                if backup and self._enforced_fields_for_logical(child, enforce_sections):
                    continue
                spec = self._section_spec(child)
                effective_now = dict(now_config or {}) if backup else await self._effective_now_config(session, child, now_config or {})
                rendered = spec.render_show_with_context(
                    effective_now,
                    deploy_config,
                    self._selected_for_section(session, child),
                )
                now_block = self._strip_group_root(self._extract_now_block(rendered), parent_section)
                deploy_block = self._strip_group_root(self._extract_deploy_block(rendered), parent_section)
                if diff_only and now_block == deploy_block:
                    continue
                changed = True
                self._append_group_block(now_root, now_block)
                self._append_group_block(deploy_root, deploy_block)
            if diff_only and not changed:
                return ""
            now_lines = render_cli_tree([now_root])
            deploy_lines = render_cli_tree([deploy_root])
            return "\n".join(["now-config:", *now_lines, "deploy-config:", *deploy_lines]).rstrip()

        root = CliNode(kind="enter", text=f"enter {parent_section}")
        for child in child_sections:
            await self._ensure_config_state(session, child)
            now_config = self._now_config_for_section(session, child)
            deploy_config = self._deploy_config_for_section(session, child)
            if backup and mode == "now" and self._enforced_fields_for_logical(child, enforce_sections):
                continue
            spec = self._section_spec(child)
            effective_now = dict(now_config or {}) if backup else await self._effective_now_config(session, child, now_config or {})
            rendered = spec.render_show_with_context(
                effective_now,
                deploy_config,
                self._selected_for_section(session, child),
            )
            block = self._render_show_by_mode(rendered, mode, diff_only=diff_only)
            child_lines = self._strip_group_root(block, parent_section)
            self._append_group_block(root, child_lines)
        return "\n".join(render_cli_tree([root])).rstrip()

    def _grouped_guild_log_children(self) -> list[str]:
        preferred = ["guild-log/message-log", "guild-log/mod-log", "guild-log/member-log"]
        available = set(self.sections.keys())
        ordered = [item for item in preferred if item in available]
        extras = sorted(item for item in available if item.startswith("guild-log/") and item not in set(ordered))
        return ordered + extras

    def _grouped_control_plane_children(self) -> list[str]:
        preferred = ["control-plane", "control-plane/root-connection", "control-plane/tick"]
        available = set(self.sections.keys())
        ordered = [item for item in preferred if item in available]
        extras = sorted(item for item in available if item.startswith("control-plane/") and item not in set(ordered))
        return ordered + extras

    def _grouped_sticky_message_sections(self) -> list[str]:
        preferred = ["sticky-message"]
        available = set(self.sections.keys())
        ordered = [item for item in preferred if item in available]
        extras = sorted(item for item in available if item.startswith("sticky-message/") and item not in set(ordered))
        if "sticky-message" in ordered:
            return ["sticky-message"]
        return ordered + extras

    def _grouped_chat_group_sections(self) -> list[str]:
        preferred = ["chat-group"]
        available = set(self.sections.keys())
        ordered = [item for item in preferred if item in available]
        extras = sorted(item for item in available if item.startswith("chat-group/") and item not in set(ordered))
        if "chat-group" in ordered:
            return ["chat-group"]
        return ordered + extras

    def _ordered_descendant_sections(self, parent_section: str) -> list[str]:
        if parent_section == "guild-log":
            return self._grouped_guild_log_children()
        if parent_section == "control-plane":
            return self._grouped_control_plane_children()
        descendants = [
            section_key
            for section_key in self._descendant_leaf_sections(parent_section)
            if section_key.startswith(parent_section + "/")
        ]
        return sorted(descendants)

    def _strip_group_root(self, block: str, root_section: str) -> list[str]:
        lines = [line for line in block.splitlines() if line]
        if lines and lines[0] == f"enter {root_section}":
            return lines[1:]
        return lines

    def _append_group_block(self, root: CliNode, lines: list[str]) -> None:
        if not lines:
            return
        parsed_nodes = self._parse_cli_nodes(lines)
        if not parsed_nodes:
            return
        for node in parsed_nodes:
            if node.text == root.text:
                root.children.extend(node.children)
            else:
                root.children.append(node)

    def _parse_cli_nodes(self, lines: list[str]) -> list[CliNode]:
        roots: list[CliNode] = []
        stack: list[CliNode] = []

        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            if line == "leave":
                if stack:
                    stack.pop()
                continue
            if line.startswith("enter "):
                node = CliNode(kind="enter", text=line)
                if stack:
                    stack[-1].children.append(node)
                else:
                    roots.append(node)
                stack.append(node)
                continue
            if line.startswith("select "):
                node = CliNode(kind="select", text=line)
                if stack:
                    stack[-1].children.append(node)
                else:
                    roots.append(node)
                stack.append(node)
                continue

            kind = "set" if line.startswith("set ") else ("comment" if line.startswith("#") else "other")
            node = CliNode(kind=kind, text=line)
            if stack:
                stack[-1].children.append(node)
            else:
                roots.append(node)

        return roots

    def _indent_lines(self, lines: list[str]) -> list[str]:
        return [f"  {line}" for line in lines]

    def _global_show_sections_for_guild(self) -> list[str]:
        return [key for key in self.sections if self._is_guild_section(key)]

    async def _build_level_table_preview(self, session: SessionContext, use_startup: bool) -> list[dict[str, Any]]:
        source = "startup" if use_startup else "running"

        common_running, common_startup = await self._ensure_config_state(session, "level-common")
        segment_running, segment_startup = await self._ensure_config_state(session, "level-segment-table")
        static_running, static_startup = await self._ensure_config_state(session, "level-static-table")

        common_cfg = common_startup if source == "startup" else common_running
        segment_cfg = segment_startup if source == "startup" else segment_running
        static_cfg = static_startup if source == "startup" else static_running

        max_level = int(common_cfg.get("max_level", 100))
        table_mode = str(common_cfg.get("level_table", "fixed"))
        rows: list[dict[str, Any]] = []

        if table_mode == "segment-interpolation":
            points: dict[int, int] = {}
            for key, value in dict(segment_cfg.get("entries", {})).items():
                try:
                    points[int(key)] = int(value)
                except ValueError:
                    continue
            if 1 not in points:
                points[1] = 0
            sorted_levels = sorted(points.keys())
            cumulative_prev = 0
            for level in range(1, max_level + 1):
                cumulative = self._segment_interpolate(level, points, sorted_levels)
                delta = cumulative if level == 1 else max(0, cumulative - cumulative_prev)
                rows.append({"level": level, "required_total_xp": int(cumulative), "delta_xp": int(delta), "segment": "segment-interpolation"})
                cumulative_prev = int(cumulative)
            return rows

        if table_mode == "static-table":
            delta_map: dict[int, int] = {}
            for key, value in dict(static_cfg.get("entries", {})).items():
                try:
                    delta_map[int(key)] = int(value)
                except ValueError:
                    continue
            cumulative = 0
            for level in range(1, max_level + 1):
                delta = int(delta_map.get(level, 0))
                cumulative += delta
                rows.append({"level": level, "required_total_xp": cumulative, "delta_xp": delta, "segment": "static-table"})
            return rows

        if table_mode == "function":
            cumulative_prev = 0
            function_type = str(common_cfg.get("function_type", "exponential"))
            for level in range(1, max_level + 1):
                if function_type == "quadratic":
                    a = float(common_cfg.get("function_a", 1.0))
                    b = float(common_cfg.get("function_b", 10.0))
                    c = float(common_cfg.get("function_c", 0.0))
                    cumulative = int(a * level * level + b * level + c)
                    segment = "function:quadratic"
                else:
                    base = float(common_cfg.get("function_base", 100))
                    rate = float(common_cfg.get("function_rate", 1.2))
                    cumulative = int(base * (rate ** (level - 1)))
                    segment = "function:exponential"
                delta = cumulative if level == 1 else max(0, cumulative - cumulative_prev)
                rows.append({"level": level, "required_total_xp": int(cumulative), "delta_xp": int(delta), "segment": segment})
                cumulative_prev = int(cumulative)
            return rows

        fixed_step = int(common_cfg.get("fixed_step", 100))
        cumulative = 0
        for level in range(1, max_level + 1):
            delta = fixed_step
            cumulative += delta
            rows.append({"level": level, "required_total_xp": cumulative, "delta_xp": delta, "segment": "fixed"})
        return rows

    def _segment_interpolate(self, level: int, points: dict[int, int], sorted_levels: list[int]) -> int:
        if level in points:
            return points[level]
        lower_candidates = [item for item in sorted_levels if item < level]
        upper_candidates = [item for item in sorted_levels if item > level]
        if not lower_candidates:
            return points[sorted_levels[0]]
        if not upper_candidates:
            return points[sorted_levels[-1]]
        lower = max(lower_candidates)
        upper = min(upper_candidates)
        lower_xp = points[lower]
        upper_xp = points[upper]
        ratio = (level - lower) / (upper - lower)
        return int(lower_xp + (upper_xp - lower_xp) * ratio)

    def _format_level_table_rows(self, rows: list[dict[str, Any]], title: str) -> str:
        lines = [title]
        if not rows:
            lines.append("(empty)")
            return "\n".join(lines)
        for row in rows:
            lines.append(
                "level={0} required_total_xp={1} delta_xp={2} segment={3}".format(
                    row.get("level"),
                    row.get("required_total_xp"),
                    row.get("delta_xp"),
                    row.get("segment"),
                )
            )
        return "\n".join(lines)

    async def _resolve_display_timezone_name(self, ctx: EngineContext, session: SessionContext) -> str:
        if session.scope_type != ScopeType.GUILD:
            return "UTC"
        running_config, _startup_config = await self._ensure_config_state(session, "control-plane")
        configured_timezone = running_config.get("timezone") if isinstance(running_config, dict) else None
        resolution = resolve_display_timezone_with_meta(getattr(ctx, "guild", None), configured_timezone)
        if resolution.unresolved_region:
            await self._log_unresolved_timezone_region_once_per_minute(
                session.scope_id,
                resolution.unresolved_region,
                resolution.timezone,
            )
        return resolution.timezone

    async def _log_unresolved_timezone_region_once_per_minute(
        self,
        guild_id: int,
        region_name: str,
        resolved_timezone: str,
    ) -> None:
        minute_key = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        cache_key = (guild_id, region_name, minute_key)
        if cache_key in self._tz_warning_minute_cache:
            return
        self._tz_warning_minute_cache.add(cache_key)
        await self.storage.insert_system_log_safe(
            actor_user_id=None,
            scope_id=guild_id,
            feature="timezone",
            severity="warn",
            message="unknown rtc_region; fallback timezone applied",
            detail_json={"rtc_region": region_name, "resolved_timezone": resolved_timezone},
        )

    def _format_timestamp_for_display(self, raw_value: Any, timezone_name: str) -> str:
        parsed = self._parse_timestamp(raw_value)
        utc_text = parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            local_zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            local_zone = timezone.utc
            timezone_name = "UTC"
        local_text = parsed.astimezone(local_zone).strftime("%Y-%m-%d %H:%M:%S")
        return f"local={local_text} {timezone_name} utc={utc_text}"

    def _parse_timestamp(self, raw_value: Any) -> datetime:
        if isinstance(raw_value, datetime):
            parsed = raw_value
        else:
            text = str(raw_value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _summarize_log_value(self, value: Any, *, max_len: int = 240) -> str:
        text = str(value if value is not None else "")
        normalized = " ".join(part for part in text.replace("\r", "\n").split("\n"))
        normalized = " ".join(normalized.split())
        if len(normalized) <= max_len:
            return normalized
        return normalized[: max_len - 3].rstrip() + "..."

    def _now_config_for_section(self, session: SessionContext, logical_section: str) -> dict[str, Any] | None:
        payload = session.running_cache.get(self._storage_section_key(logical_section))
        return self._logical_payload(payload, logical_section)

    def _deploy_config_for_section(self, session: SessionContext, logical_section: str) -> dict[str, Any] | None:
        payload = session.startup_cache.get(self._storage_section_key(logical_section))
        return self._logical_payload(payload, logical_section)

    def _counter_section(self, session: SessionContext) -> str:
        return self._current_section_key(session) or "global"

    def _current_section_key(self, session: SessionContext) -> str | None:
        if not session.current_path:
            return None
        full_path = "/".join(session.current_path)
        if self._is_leaf_section(full_path):
            return full_path
        return None

    def _storage_section_key(self, section_key: str) -> str:
        if section_key.startswith("sticky-message/"):
            return "sticky-message"
        if section_key.startswith("guild-log/"):
            return "guild-log"
        if section_key.startswith("root-enforce-override/"):
            return "root-enforce-override"
        if section_key.startswith("root-enforce/"):
            return "root-enforce"
        if section_key.startswith("root-defaults/"):
            return "root-defaults"
        if section_key.startswith("control-plane/"):
            return "control-plane"
        if section_key.startswith("chat-group/"):
            return "chat-group"
        if section_key.startswith("tenant-connection/"):
            return "tenant-connection"
        return section_key

    def _section_spec(self, section_key: str) -> SectionSpec:
        spec = self.sections.get(section_key)
        if not spec:
            raise SectionError(f"field=section reason=unknown section hint={section_key}")
        return spec

    def _is_leaf_section(self, section_key: str) -> bool:
        return section_key in self.sections

    def _is_guild_section(self, section_key: str) -> bool:
        return (
            section_key not in {"root-defaults", "root-enforce", "root-enforce-override"}
            and not section_key.startswith("root-defaults/")
            and not section_key.startswith("root-enforce/")
            and not section_key.startswith("root-enforce-override/")
            and not section_key.startswith("tenant-connection/")
            and not section_key.startswith("chat-group-global/")
            and section_key != "chat-group-global"
        )

    def _path_allowed(self, path: str, scope_type: ScopeType, is_bot_admin: bool) -> bool:
        if self._is_leaf_section(path):
            if (
                path in {"root-defaults", "root-enforce", "root-enforce-override"}
                or path.startswith("root-defaults/")
                or path.startswith("root-enforce/")
                or path.startswith("root-enforce-override/")
            ):
                return scope_type == ScopeType.ROOT and is_bot_admin
            if path.startswith("tenant-connection/"):
                return scope_type == ScopeType.ROOT and is_bot_admin
            if path == "chat-group-global" or path.startswith("chat-group-global/"):
                return scope_type == ScopeType.ROOT and is_bot_admin
            return scope_type == ScopeType.GUILD

        descendants = self._descendant_leaf_sections(path)
        if not descendants:
            return False
        return any(self._path_allowed(section, scope_type, is_bot_admin) for section in descendants)

    def _descendant_leaf_sections(self, parent_path: str) -> list[str]:
        prefix = parent_path + "/"
        return [key for key in self.sections if key.startswith(prefix)]

    def _build_section_children(self) -> dict[str, set[str]]:
        tree: dict[str, set[str]] = {"": set()}
        for section_key in self.sections:
            parts = section_key.split("/")
            parent = ""
            for part in parts:
                tree.setdefault(parent, set()).add(part)
                parent = f"{parent}/{part}" if parent else part
                tree.setdefault(parent, set())
        return tree

    def _build_storage_spec_lookup(self) -> dict[str, str]:
        lookup: dict[str, str] = {}
        for section_key in self.sections:
            storage_key = self._storage_section_key(section_key)
            lookup.setdefault(storage_key, section_key)
        return lookup

    def _logical_payload(self, payload: Any, logical_section: str) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None

        storage_key = self._storage_section_key(logical_section)
        if storage_key == logical_section:
            return dict(payload)
        if storage_key == "sticky-message" and logical_section.startswith("sticky-message/"):
            return dict(payload)

        remainder = logical_section[len(storage_key) + 1 :]
        current: Any = payload
        for token in remainder.split("/"):
            if not isinstance(current, dict):
                return None
            current = current.get(token.replace("-", "_"))
        return dict(current) if isinstance(current, dict) else None

    def _extract_now_block(self, rendered: str) -> str:
        text = rendered.strip()
        if text.startswith("now-config:\n"):
            text = text[len("now-config:\n") :]
        elif text == "now-config:":
            text = ""
        if "\ndeploy-config:" in text:
            text = text.split("\ndeploy-config:", 1)[0]
        return text.rstrip()

    def _extract_deploy_block(self, rendered: str) -> str:
        text = rendered.strip()
        marker = "deploy-config:"
        if marker not in text:
            return ""
        suffix = text.split(marker, 1)[1]
        return suffix.lstrip("\n").rstrip()

    def _extract_diff_block(self, rendered: str) -> str:
        text = rendered.strip()
        if text.startswith("now-config:\n") or text == "now-config:":
            return text
        now_block = self._extract_now_block(rendered)
        deploy_block = self._extract_deploy_block(rendered)
        lines = ["now-config:"]
        lines.extend(now_block.split("\n") if now_block else ["# no settings"])
        lines.append("deploy-config:")
        lines.extend(deploy_block.split("\n") if deploy_block else ["# no settings"])
        return "\n".join(lines).rstrip()

    def _render_show_by_mode(self, rendered: str, mode: str, *, diff_only: bool = False) -> str:
        if mode == "now":
            return self._extract_now_block(rendered)
        if mode == "deploy":
            return self._extract_deploy_block(rendered)
        if diff_only:
            return self._extract_diff_only_block(rendered)
        return self._extract_diff_block(rendered)

    def _extract_diff_only_block(self, rendered: str) -> str:
        now_block = self._extract_now_block(rendered).strip()
        deploy_block = self._extract_deploy_block(rendered).strip()
        if now_block == deploy_block:
            return ""
        lines = ["now-config:"]
        lines.extend(now_block.split("\n") if now_block else ["# no settings"])
        lines.append("deploy-config:")
        lines.extend(deploy_block.split("\n") if deploy_block else ["# no settings"])
        return "\n".join(lines).rstrip()

    async def _effective_now_config(self, session: SessionContext, logical_section: str, now_config: dict[str, Any]) -> dict[str, Any]:
        if session.scope_type != ScopeType.GUILD:
            return now_config
        enforce_sections = await self._effective_root_enforce_sections_for_guild(session.scope_id)
        if not enforce_sections:
            return now_config
        return self._apply_root_enforce_overlay(logical_section, now_config, enforce_sections)

    async def _root_enforce_sections(self) -> dict[str, dict[str, Any]]:
        enforce_state = await self._load_section_payload(ScopeType.ROOT, 0, "root-enforce")
        enforce_payload = enforce_state[0] if enforce_state else None
        if not isinstance(enforce_payload, dict):
            return {}
        sections = enforce_payload.get("sections", {})
        if not isinstance(sections, dict):
            return {}
        return {str(key): value for key, value in sections.items() if isinstance(value, dict)}

    async def _effective_root_enforce_sections_for_guild(self, guild_id: int) -> dict[str, dict[str, Any]]:
        merged = await self._root_enforce_sections()
        override_state = await self._load_section_payload(ScopeType.ROOT, 0, "root-enforce-override")
        override_payload = override_state[0] if override_state else None
        if not isinstance(override_payload, dict):
            return merged
        guilds = override_payload.get("guilds", {})
        if not isinstance(guilds, dict):
            return merged
        entry = guilds.get(str(guild_id), {})
        sections = entry.get("sections", {}) if isinstance(entry, dict) else {}
        if not isinstance(sections, dict):
            return merged
        for section_key, fields in sections.items():
            if not isinstance(fields, dict):
                continue
            base = dict(merged.get(section_key, {}))
            base.update(fields)
            merged[section_key] = base
        return self._normalize_policy_sections(merged)

    def _normalize_policy_sections(self, sections: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for section_key, fields in sections.items():
            if not isinstance(fields, dict):
                continue
            base = dict(normalized.get(section_key, {}))
            for key, value in fields.items():
                if not isinstance(key, str) or "." not in key:
                    base[key] = value
                    continue
                head, tail = key.split(".", 1)
                nested_section = f"{section_key}/{head}"
                nested = dict(normalized.get(nested_section, {}))
                nested[tail] = value
                normalized[nested_section] = nested
            normalized[section_key] = base
        return normalized

    def _apply_root_enforce_overlay(
        self, logical_section: str, now_config: dict[str, Any], enforce_sections: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        result = dict(now_config)
        fields = self._enforced_fields_for_logical(logical_section, enforce_sections)
        for raw_key, raw_value in fields.items():
            path = [token.replace("-", "_") for token in str(raw_key).split(".") if token]
            if not path:
                continue
            cursor = result
            for token in path[:-1]:
                child = cursor.get(token)
                if not isinstance(child, dict):
                    child = {}
                    cursor[token] = child
                cursor = child
            cursor[path[-1]] = raw_value
        return result

    def _enforced_fields_for_logical(
        self, logical_section: str, enforce_sections: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        direct = enforce_sections.get(logical_section)
        if isinstance(direct, dict):
            merged.update(direct)

        storage_key = self._storage_section_key(logical_section)
        storage_fields = enforce_sections.get(storage_key)
        if isinstance(storage_fields, dict):
            if storage_key == logical_section:
                merged.update(storage_fields)
            else:
                remainder = logical_section[len(storage_key) + 1 :].replace("/", ".")
                prefix = remainder + "."
                for key, value in storage_fields.items():
                    if isinstance(key, str) and key.startswith(prefix):
                        merged[key[len(prefix) :]] = value
        return merged

    async def _render_root_enforce_block_for_guild(self, session: SessionContext) -> str:
        enforce_sections = await self._effective_root_enforce_sections_for_guild(session.scope_id)
        return "\n".join(render_cli_tree([build_sections_tree("root-enforce", enforce_sections)]))

    def _parse_show_mode(self, args: list[str]) -> tuple[str, bool, bool]:
        if not args:
            return "now", False, False
        if len(args) > 2:
            raise SectionError("field=show reason=invalid args hint=show [now-config [backup]|deploy-config|diff-config [diff-only]]")
        token = args[0]
        if token == "now-config":
            if len(args) == 1:
                return "now", False, False
            if args[1] == "backup":
                return "now", False, True
            raise SectionError("field=show reason=invalid args hint=show [now-config [backup]|deploy-config|diff-config [diff-only]]")
        if token == "deploy-config":
            return "deploy", False, False
        if token == "diff-config":
            if len(args) == 1:
                return "diff", False, False
            if args[1] == "diff-only":
                return "diff", True, False
        raise SectionError("field=show reason=invalid args hint=show [now-config [backup]|deploy-config|diff-config [diff-only]]")

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
        suffix = uuid.uuid4().hex[:12]
        return f"CR-{now}-{suffix}"

    async def _format_level_user_snapshot(self, guild_id: int, user_id: int) -> str:
        row = await self.storage.get_level_user(guild_id, user_id)
        total_xp = row.total_xp if row else 0
        level = row.level if row else 0
        ranking = await self.storage.fetch_level_ranking(guild_id, 1000)
        rank = next((index + 1 for index, item in enumerate(ranking) if item.user_id == user_id), None)
        next_level_xp = None
        for entry in await self.storage.fetch_level_table(guild_id):
            threshold = int(entry.get("required_total_xp", 0))
            if threshold > total_xp:
                next_level_xp = threshold
                break
        if next_level_xp is None:
            next_level_xp = (level + 1) * 100
        rank_text = str(rank) if rank is not None else "-"
        return f"user={user_id} level={level} total_xp={total_xp} next_level_xp={next_level_xp} rank={rank_text}"

    async def _resolve_section_context(
        self,
        session: SessionContext,
        required_field: str,
        required_hint: str,
        *,
        required_section: str | None = None,
        require_select_support: bool = False,
    ) -> tuple[str, SectionSpec, str, dict[str, Any], dict[str, Any]]:
        section_key = self._current_section_key(session)
        if not section_key:
            raise SectionError(f"field={required_field} reason=no active section hint={required_hint}")
        if required_section and section_key != required_section:
            raise SectionError(f"field={required_field} reason=invalid context hint={required_hint}")

        spec = self._section_spec(section_key)
        if require_select_support and not spec.supports_select():
            raise SectionError("field=select reason=invalid context hint=select not supported in this section")

        storage_key = self._storage_section_key(section_key)
        running_config, startup_config = await self._ensure_config_state(session, section_key)
        return section_key, spec, storage_key, running_config, startup_config
