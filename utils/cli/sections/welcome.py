from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import FieldRule, MappedSectionSpec
from utils.cli.types import WelcomeConfigV1


class WelcomeSection(MappedSectionSpec):
    name = "welcome"
    schema_version = 1
    field_rules = {
        "join-roles": FieldRule(
            path=("join_roles",),
            parser=MappedSectionSpec.parse_nonempty_int_list(
                "join-roles",
                empty_hint="provide one or more role ids",
                invalid_hint="use numeric role ids",
            ),
            candidates=["<role-id...>"],
        ),
        "welcome-message": FieldRule(
            path=("welcome_message",),
            parser=MappedSectionSpec.parse_single_string("welcome-message", hint="use quoted string"),
            candidates=['"<text>"'],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return WelcomeConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return WelcomeConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)
