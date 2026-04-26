from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import render_config_pair
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

    def list_set_keys(self) -> list[str]:
        return sorted(self.allowed_features)

    def list_value_candidates(self, key: str) -> list[str]:
        if key in self.allowed_features:
            return ["debug", "info", "warn", "error"]
        return []

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def to_cli_payload(source: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(source, dict):
                return None
            levels = source.get("levels")
            if not isinstance(levels, dict):
                return None
            return {key: value for key, value in levels.items()}

        return render_config_pair(self.name, to_cli_payload(now_config), to_cli_payload(deploy_config))
