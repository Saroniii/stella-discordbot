from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import FieldRule, MappedSectionSpec, SectionError
from utils.cli.types import ConsoleConfigV1


def _parse_thread_prefix(values: list[str]) -> str:
    if len(values) != 1:
        raise SectionError("field=thread-prefix reason=invalid value count hint=one prefix")
    value = values[0]
    if not value:
        raise SectionError("field=thread-prefix reason=empty value hint=use non-empty prefix")
    return value


class ConsoleSection(MappedSectionSpec):
    name = "console"
    schema_version = 1
    field_rules = {
        "always-print-help": FieldRule(
            path=("always_print_help",),
            parser=MappedSectionSpec.parse_enable_disable("always-print-help"),
            candidates=["enable", "disable"],
        ),
        "console-mode": FieldRule(
            path=("console_mode",),
            parser=MappedSectionSpec.parse_single_choice("console-mode", {"thread", "channel"}, "thread|channel"),
            candidates=["thread", "channel"],
        ),
        "thread-console-after-delete": FieldRule(
            path=("thread_console_after_delete",),
            parser=MappedSectionSpec.parse_enable_disable("thread-console-after-delete"),
            candidates=["enable", "disable"],
        ),
        "session-timeout-sec": FieldRule(
            path=("session_timeout_sec",),
            parser=MappedSectionSpec.parse_single_int("session-timeout-sec", hint="one integer"),
            candidates=["<30..86400>"],
        ),
        "thread-delete-delay-sec": FieldRule(
            path=("thread_delete_delay_sec",),
            parser=MappedSectionSpec.parse_single_int("thread-delete-delay-sec", hint="one integer"),
            candidates=["<0..3600>"],
        ),
        "thread-prefix": FieldRule(
            path=("thread_prefix",),
            parser=_parse_thread_prefix,
            candidates=["<prefix>"],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return ConsoleConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ConsoleConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        canonical = self._canonical_key(key)
        if canonical in {"session-timeout-sec", "thread-delete-delay-sec", "thread-prefix"}:
            draft = self._copy_payload(payload)
            self._set_path(draft, self.field_rules[canonical].path, None)
            return self.validate_payload(draft)
        return super().apply_unset(payload, key)
