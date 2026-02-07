from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.types import WelcomeConfigV1


class WelcomeSection(SectionSpec):
    name = "welcome"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return WelcomeConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return WelcomeConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        draft = deepcopy(payload)
        if key == "join-roles":
            if not values:
                raise SectionError("field=join-roles reason=empty value hint=provide one or more role ids")
            try:
                draft["join_roles"] = [int(value) for value in values]
            except ValueError as exc:
                raise SectionError("field=join-roles reason=invalid integer hint=use numeric role ids") from exc
        elif key == "welcome-message":
            if len(values) != 1:
                raise SectionError("field=welcome-message reason=invalid value count hint=use quoted string")
            draft["welcome_message"] = values[0]
        else:
            raise SectionError(f"field={key} reason=unknown key hint=allowed: join-roles,welcome-message")

        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        draft = deepcopy(payload)
        if key == "join-roles":
            draft["join_roles"] = []
        elif key == "welcome-message":
            draft["welcome_message"] = None
        else:
            raise SectionError(f"field={key} reason=unknown key hint=allowed: join-roles,welcome-message")
        return self.validate_payload(draft)
