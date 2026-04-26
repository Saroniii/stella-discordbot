from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import FieldRule, MappedSectionSpec
from utils.cli.types import ConsoleConfigV1


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
    }

    def default_payload(self) -> dict[str, Any]:
        return ConsoleConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ConsoleConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)
