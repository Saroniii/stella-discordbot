from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import FieldRule, MappedSectionSpec
from utils.cli.types import LogConfigV1


class LogConfigSection(MappedSectionSpec):
    name = "log-config"
    schema_version = 1
    field_rules = {
        "audit-log-max-buffer": FieldRule(
            path=("audit_log_max_buffer",),
            parser=MappedSectionSpec.parse_single_int(
                "audit-log-max-buffer",
                hint="provide exactly one integer",
            ),
            candidates=["<100..100000>"],
        ),
        "system-log-max-buffer": FieldRule(
            path=("system_log_max_buffer",),
            parser=MappedSectionSpec.parse_single_int(
                "system-log-max-buffer",
                hint="provide exactly one integer",
            ),
            candidates=["<100..100000>"],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return LogConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return LogConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)
