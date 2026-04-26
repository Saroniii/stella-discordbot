from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from utils.cli.formatter import render_config_pair, section_to_enter_path
from utils.cli.sections.base import FieldRule, MappedSectionSpec, SectionError
from utils.cli.types import ControlPlaneConfigV1


def _parse_timezone(values: list[str]) -> str:
    if len(values) != 1:
        raise SectionError("field=timezone reason=invalid value count hint=use one IANA timezone")
    timezone_name = values[0].strip()
    if not timezone_name:
        raise SectionError("field=timezone reason=invalid value hint=use IANA timezone (e.g. Asia/Tokyo)")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SectionError("field=timezone reason=invalid timezone hint=use IANA timezone (e.g. Asia/Tokyo)") from exc
    return timezone_name


class ControlPlaneSection(MappedSectionSpec):
    name = "control-plane"
    schema_version = 1
    field_rules = {
        "timezone": FieldRule(
            path=("timezone",),
            parser=_parse_timezone,
            candidates=["UTC", "Asia/Tokyo", "America/Los_Angeles", "Europe/London"],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return ControlPlaneConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ControlPlaneConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def to_cli_payload(source: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(source, dict):
                return None
            return {"timezone": source.get("timezone")}

        return render_config_pair(
            self.name,
            to_cli_payload(now_config),
            to_cli_payload(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )


class ControlPlaneRootConnectionSection(MappedSectionSpec):
    name = "control-plane/root-connection"
    schema_version = 1
    field_rules = {
        "send-crashlog-root": FieldRule(
            path=("root_connection", "send_crashlog_root"),
            parser=MappedSectionSpec.parse_enable_disable("send-crashlog-root"),
            candidates=["enable", "disable"],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return ControlPlaneConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ControlPlaneConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def to_cli_payload(source: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(source, dict):
                return None
            if "send_crashlog_root" in source:
                return {"send_crashlog_root": source.get("send_crashlog_root")}
            root_connection = source.get("root_connection")
            if isinstance(root_connection, dict):
                return {"send_crashlog_root": root_connection.get("send_crashlog_root")}
            return None

        return render_config_pair(
            self.name,
            to_cli_payload(now_config),
            to_cli_payload(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )


class ControlPlaneTickSection(MappedSectionSpec):
    name = "control-plane/tick"
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
        return ControlPlaneConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ControlPlaneConfigV1.model_validate(payload).model_dump(mode="json")
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
