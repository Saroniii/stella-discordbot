from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.types import LogTypeConfigV1


class LogTypeSection(SectionSpec):
    name = "log-type"
    schema_version = 1

    def __init__(self, allowed_features: set[str]) -> None:
        self.allowed_features = allowed_features

    def default_payload(self) -> dict[str, Any]:
        return LogTypeConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return LogTypeConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if key not in self.allowed_features:
            raise SectionError(f"field={key} reason=unknown feature hint=use registered section name")
        if len(values) != 1:
            raise SectionError("field=severity reason=invalid value count hint=use one value")
        level = values[0].lower()
        if level not in {"debug", "info", "warn", "error"}:
            raise SectionError("field=severity reason=invalid level hint=debug|info|warn|error")

        draft = deepcopy(payload)
        levels = dict(draft.get("levels", {}))
        levels[key] = level
        draft["levels"] = levels
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        draft = deepcopy(payload)
        levels = dict(draft.get("levels", {}))
        levels.pop(key, None)
        draft["levels"] = levels
        return self.validate_payload(draft)
