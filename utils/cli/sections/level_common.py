from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import FieldRule, MappedSectionSpec, SectionError
from utils.cli.types import LevelCommonConfigV1


class LevelCommonSection(MappedSectionSpec):
    name = "level-common"
    schema_version = 1
    field_rules = {
        "level-calc": FieldRule(
            path=("level_calc",),
            parser=MappedSectionSpec.parse_single_choice("level-calc", {"message-count", "char-count"}, "message-count|char-count"),
            candidates=["message-count", "char-count"],
        ),
        "level-table": FieldRule(
            path=("level_table",),
            parser=MappedSectionSpec.parse_single_choice(
                "level-table",
                {"fixed", "function", "segment-interpolation", "static-table"},
                "fixed|function|segment-interpolation|static-table",
            ),
            candidates=["fixed", "function", "segment-interpolation", "static-table"],
        ),
        "gain-policy": FieldRule(
            path=("gain_policy",),
            parser=MappedSectionSpec.parse_enable_disable("gain-policy"),
            candidates=["enable", "disable"],
        ),
        "max-level": FieldRule(
            path=("max_level",),
            parser=MappedSectionSpec.parse_single_int("max-level", "use one value"),
            candidates=["<1..1000>"],
        ),
        "gain-time": FieldRule(
            path=("gain_time",),
            parser=MappedSectionSpec.parse_single_int("gain-time", "use one value"),
            candidates=["<seconds>"],
        ),
        "min-char-count": FieldRule(
            path=("min_char_count",),
            parser=MappedSectionSpec.parse_single_int("min-char-count", "use one value"),
            candidates=["<0..10000>"],
        ),
        "fixed-step": FieldRule(
            path=("fixed_step",),
            parser=MappedSectionSpec.parse_single_int("fixed-step", "use one value"),
            candidates=["<int>"],
        ),
        "function-type": FieldRule(
            path=("function_type",),
            parser=MappedSectionSpec.parse_single_choice("function-type", {"exponential", "quadratic"}, "exponential|quadratic"),
            candidates=["exponential", "quadratic"],
        ),
        "multiplier": FieldRule(
            path=("multiplier",),
            parser=lambda values: _parse_single_float("multiplier", values),
            candidates=["<float>"],
        ),
        "function-base": FieldRule(
            path=("function_base",),
            parser=lambda values: _parse_single_int("function-base", values),
            candidates=["<int>"],
        ),
        "function-rate": FieldRule(
            path=("function_rate",),
            parser=lambda values: _parse_single_float("function-rate", values),
            candidates=["<float>"],
        ),
        "function-a": FieldRule(
            path=("function_a",),
            parser=lambda values: _parse_single_float("function-a", values),
            candidates=["<float>"],
        ),
        "function-b": FieldRule(
            path=("function_b",),
            parser=lambda values: _parse_single_float("function-b", values),
            candidates=["<float>"],
        ),
        "function-c": FieldRule(
            path=("function_c",),
            parser=lambda values: _parse_single_float("function-c", values),
            candidates=["<float>"],
        ),
        "levelup-channel": FieldRule(
            path=("levelup_channel",),
            parser=lambda values: _parse_channel_or_none(values),
            candidates=["<channel-id>", "none"],
        ),
        "levelup-message": FieldRule(
            path=("levelup_message",),
            parser=MappedSectionSpec.parse_single_string("levelup-message", 'set levelup-message "<template>"'),
            candidates=[
                '"{mention} is now level {level}!"',
                '"{nickname} leveled up to {level}!"',
                '"GG {username}! total xp: {total_xp}"',
            ],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return LevelCommonConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return LevelCommonConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)


def _parse_single_float(field: str, values: list[str]) -> float:
    if len(values) != 1:
        raise SectionError(f"field={field} reason=invalid value count hint=use one value")
    try:
        return float(values[0])
    except ValueError as exc:
        raise SectionError(f"field={field} reason=invalid number hint=use numeric value") from exc


def _parse_single_int(field: str, values: list[str]) -> int:
    if len(values) != 1:
        raise SectionError(f"field={field} reason=invalid value count hint=use one value")
    try:
        return int(values[0])
    except ValueError as exc:
        raise SectionError(f"field={field} reason=invalid integer hint=use numeric value") from exc


def _parse_channel_or_none(values: list[str]) -> int | None:
    if len(values) != 1:
        raise SectionError("field=levelup-channel reason=invalid value count hint=<channel-id>|none")
    if values[0].lower() == "none":
        return None
    try:
        return int(values[0])
    except ValueError as exc:
        raise SectionError("field=levelup-channel reason=invalid integer hint=use numeric channel id or none") from exc
