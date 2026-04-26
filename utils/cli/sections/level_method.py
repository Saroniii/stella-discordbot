from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import render_config_pair
from utils.cli.sections.base import FieldRule, MappedSectionSpec, SectionError
from utils.cli.types import LevelMethodConfigV1


class LevelMethodSection(MappedSectionSpec):
    schema_version = 1

    def __init__(self, method_name: str) -> None:
        self.method_name = method_name
        self.name = f"level-method-{method_name}"
        self.field_rules = {
            "gain-mode": FieldRule(
                path=("gain_mode",),
                parser=MappedSectionSpec.parse_single_choice("gain-mode", {"static", "random-range"}, "static|random-range"),
                candidates=["static", "random-range"],
            ),
            "gain-xp": FieldRule(
                path=("gain_xp",),
                parser=MappedSectionSpec.parse_single_int("gain-xp", "use one integer"),
                candidates=["<int>"],
            ),
            "gain-time": FieldRule(
                path=("gain_time",),
                parser=MappedSectionSpec.parse_single_int("gain-time", "use one integer"),
                candidates=["<seconds>"],
            ),
            "gain-range": FieldRule(
                path=("gain_range_min",),
                parser=_parse_gain_range,
                candidates=["min", "<int>", "max", "<int>"],
            ),
        }

    def default_payload(self) -> dict[str, Any]:
        return LevelMethodConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = LevelMethodConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)
        if validated["gain_mode"] == "random-range" and validated["gain_range_min"] > validated["gain_range_max"]:
            raise SectionError("field=gain-range reason=invalid range hint=min must be <= max")
        return validated

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if key == "gain-range":
            draft = self._copy_payload(payload)
            min_value, max_value = _parse_gain_range(values)
            draft["gain_range_min"] = min_value
            draft["gain_range_max"] = max_value
            return self.validate_payload(draft)
        return super().validate_set(payload, key, values)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        if key == "gain-range":
            draft = self._copy_payload(payload)
            default = self.default_payload()
            draft["gain_range_min"] = default["gain_range_min"]
            draft["gain_range_max"] = default["gain_range_max"]
            return self.validate_payload(draft)
        return super().apply_unset(payload, key)

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def to_cli_payload(source: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(source, dict):
                return None
            payload = {
                "gain_mode": source.get("gain_mode"),
                "gain_xp": source.get("gain_xp"),
                "gain_time": source.get("gain_time"),
            }
            min_value = source.get("gain_range_min")
            max_value = source.get("gain_range_max")
            if min_value is not None and max_value is not None:
                payload["gain_range"] = ["min", int(min_value), "max", int(max_value)]
            return payload

        return render_config_pair(self.name, to_cli_payload(now_config), to_cli_payload(deploy_config))


def _parse_gain_range(values: list[str]) -> tuple[int, int]:
    if len(values) != 4 or values[0] != "min" or values[2] != "max":
        raise SectionError("field=gain-range reason=invalid format hint=set gain-range min <n> max <n>")
    try:
        return int(values[1]), int(values[3])
    except ValueError as exc:
        raise SectionError("field=gain-range reason=invalid number hint=use numeric value") from exc
