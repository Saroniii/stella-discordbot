from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.types import LogConfigV1


class LogConfigSection(SectionSpec):
    name = "log-config"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return LogConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return LogConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if len(values) != 1:
            raise SectionError(f"field={key} reason=invalid value count hint=provide exactly one integer")
        draft = deepcopy(payload)
        try:
            val = int(values[0])
        except ValueError as exc:
            raise SectionError(f"field={key} reason=invalid integer hint=use numeric value") from exc

        if key == "audit-log-max-buffer":
            draft["audit_log_max_buffer"] = val
        elif key == "system-log-max-buffer":
            draft["system_log_max_buffer"] = val
        else:
            raise SectionError("field={0} reason=unknown key hint=allowed: audit-log-max-buffer,system-log-max-buffer".format(key))

        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        draft = deepcopy(payload)
        default = self.default_payload()
        if key == "audit-log-max-buffer":
            draft["audit_log_max_buffer"] = default["audit_log_max_buffer"]
        elif key == "system-log-max-buffer":
            draft["system_log_max_buffer"] = default["system_log_max_buffer"]
        else:
            raise SectionError("field={0} reason=unknown key hint=allowed: audit-log-max-buffer,system-log-max-buffer".format(key))
        return self.validate_payload(draft)
