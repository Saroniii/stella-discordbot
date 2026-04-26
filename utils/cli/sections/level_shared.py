from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import FieldRule, MappedSectionSpec
from utils.cli.types import LevelSharedConfigV1


class LevelSharedSection(MappedSectionSpec):
    name = "level-shared"
    schema_version = 1
    field_rules = {
        "mode": FieldRule(
            path=("mode",),
            parser=MappedSectionSpec.parse_single_choice("mode", {"blacklist", "whitelist"}, "blacklist|whitelist"),
            candidates=["blacklist", "whitelist"],
        ),
        "channels": FieldRule(
            path=("channels",),
            parser=MappedSectionSpec.parse_nonempty_int_list(
                "channels",
                empty_hint="use one or more channel ids",
                invalid_hint="use numeric id",
            ),
            candidates=["<channel-id...>"],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return LevelSharedConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return LevelSharedConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)
