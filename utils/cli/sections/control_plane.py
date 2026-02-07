from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.types import ControlPlaneConfigV1


class ControlPlaneRootConnectionSection(SectionSpec):
    name = "control-plane/root-connection"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return ControlPlaneConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ControlPlaneConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if key != "send-crashlog-root":
            raise SectionError("field={0} reason=unknown key hint=allowed: send-crashlog-root".format(key))
        if len(values) != 1:
            raise SectionError("field=send-crashlog-root reason=invalid value count hint=enable|disable")
        value = values[0].lower()
        if value not in {"enable", "disable"}:
            raise SectionError("field=send-crashlog-root reason=invalid value hint=enable|disable")

        draft = deepcopy(payload)
        draft["root_connection"]["send_crashlog_root"] = value == "enable"
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        if key != "send-crashlog-root":
            raise SectionError("field={0} reason=unknown key hint=allowed: send-crashlog-root".format(key))
        draft = deepcopy(payload)
        draft["root_connection"]["send_crashlog_root"] = False
        return self.validate_payload(draft)
