from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import render_config_pair, section_to_enter_path
from utils.cli.sections.base import FieldRule, MappedSectionSpec
from utils.cli.types import TenantConnectionConfigV1


def _normalize_tenant_connection_payload(payload: dict[str, Any]) -> dict[str, Any]:
    draft = dict(payload)
    tick = draft.get("tick")
    if isinstance(tick, dict):
        tick_copy = dict(tick)
        limit = tick_copy.get("max_tick_limit")
        if isinstance(limit, int):
            tick_copy["max_tick_limit"] = max(100, min(limit, 1000000))
        draft["tick"] = tick_copy
    return draft


class TenantConnectionLogSection(MappedSectionSpec):
    name = "tenant-connection/log"
    schema_version = 1
    field_rules = {
        "crashlog-report-channel": FieldRule(
            path=("log", "crashlog_report_channel"),
            parser=MappedSectionSpec.parse_single_int("crashlog-report-channel", hint="one channel id"),
            candidates=["<channel-id>"],
        ),
        "receive-mode": FieldRule(
            path=("log", "receive_mode"),
            parser=MappedSectionSpec.parse_single_choice(
                "receive-mode",
                {"off", "discord", "database", "both"},
                "off|discord|database|both",
            ),
            candidates=["off", "discord", "database", "both"],
        ),
        "crashlog-max-buffer": FieldRule(
            path=("log", "crashlog_max_buffer"),
            parser=MappedSectionSpec.parse_single_int("crashlog-max-buffer", hint="one integer"),
            candidates=["<100..100000>"],
        ),
    }
    field_aliases = {"recive-mode": "receive-mode"}

    def default_payload(self) -> dict[str, Any]:
        return TenantConnectionConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            normalized = _normalize_tenant_connection_payload(payload)
            return TenantConnectionConfigV1.model_validate(normalized).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def to_cli_payload(source: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(source, dict):
                return None
            if any(key in source for key in {"crashlog_report_channel", "receive_mode", "crashlog_max_buffer"}):
                return {
                    "crashlog_report_channel": source.get("crashlog_report_channel"),
                    "receive_mode": source.get("receive_mode"),
                    "crashlog_max_buffer": source.get("crashlog_max_buffer"),
                }
            log_data = source.get("log")
            if isinstance(log_data, dict):
                return {
                    "crashlog_report_channel": log_data.get("crashlog_report_channel"),
                    "receive_mode": log_data.get("receive_mode"),
                    "crashlog_max_buffer": log_data.get("crashlog_max_buffer"),
                }
            return None

        return render_config_pair(
            self.name,
            to_cli_payload(now_config),
            to_cli_payload(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )


class TenantConnectionTickSection(MappedSectionSpec):
    name = "tenant-connection/tick"
    schema_version = 1
    field_rules = {
        "max-tick-limit": FieldRule(
            path=("tick", "max_tick_limit"),
            parser=MappedSectionSpec.parse_single_int("max-tick-limit", hint="one integer"),
            candidates=["<100..1000000>"],
        ),
        "overlimit-mode": FieldRule(
            path=("tick", "overlimit_mode"),
            parser=MappedSectionSpec.parse_single_choice(
                "overlimit-mode",
                {"alert-only", "drop-new-work"},
                "alert-only|drop-new-work",
            ),
            candidates=["alert-only", "drop-new-work"],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return TenantConnectionConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            normalized = _normalize_tenant_connection_payload(payload)
            return TenantConnectionConfigV1.model_validate(normalized).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def to_cli_payload(source: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(source, dict):
                return None
            if "max_tick_limit" in source or "overlimit_mode" in source:
                return {
                    "max_tick_limit": source.get("max_tick_limit"),
                    "overlimit_mode": source.get("overlimit_mode"),
                }
            tick_data = source.get("tick")
            if isinstance(tick_data, dict):
                return {
                    "max_tick_limit": tick_data.get("max_tick_limit"),
                    "overlimit_mode": tick_data.get("overlimit_mode"),
                }
            return None

        return render_config_pair(
            self.name,
            to_cli_payload(now_config),
            to_cli_payload(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )
