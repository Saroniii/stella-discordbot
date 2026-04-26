from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import FieldRule, MappedSectionSpec
from utils.cli.types import ManagementModuleConfigV1


class ManagementModuleSection(MappedSectionSpec):
    name = "management-module"
    schema_version = 1
    field_rules = {
        "welcome": FieldRule(
            path=("welcome",),
            parser=MappedSectionSpec.parse_enable_disable("welcome"),
            candidates=["enable", "disable"],
        ),
        "level": FieldRule(
            path=("level",),
            parser=MappedSectionSpec.parse_enable_disable("level"),
            candidates=["enable", "disable"],
        ),
        "sticky-message": FieldRule(
            path=("sticky_message",),
            parser=MappedSectionSpec.parse_enable_disable("sticky-message"),
            candidates=["enable", "disable"],
        ),
        "auto-reaction": FieldRule(
            path=("auto_reaction",),
            parser=MappedSectionSpec.parse_enable_disable("auto-reaction"),
            candidates=["enable", "disable"],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return ManagementModuleConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ManagementModuleConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)
