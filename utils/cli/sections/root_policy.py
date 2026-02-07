from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.types import RootDefaultsConfigV1, RootEnforceConfigV1


def _convert_value(values: list[str]) -> Any:
    if not values:
        raise SectionError("field=value reason=missing value hint=provide a value")
    if len(values) == 1:
        value = values[0]
        if value.isdigit():
            return int(value)
        return value
    converted: list[Any] = []
    for value in values:
        if value.isdigit():
            converted.append(int(value))
        else:
            converted.append(value)
    return converted


class RootDefaultsSection(SectionSpec):
    name = "root-defaults"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return RootDefaultsConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return RootDefaultsConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if "." not in key:
            raise SectionError("field=key reason=invalid format hint=use section.field")
        section_name, field_name = key.split(".", 1)
        draft = deepcopy(payload)
        section = dict(draft["sections"].get(section_name, {}))
        section[field_name] = _convert_value(values)
        draft["sections"][section_name] = section
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        draft = deepcopy(payload)
        if "." in key:
            section_name, field_name = key.split(".", 1)
            section = dict(draft["sections"].get(section_name, {}))
            section.pop(field_name, None)
            if section:
                draft["sections"][section_name] = section
            else:
                draft["sections"].pop(section_name, None)
        else:
            draft["sections"].pop(key, None)
        return self.validate_payload(draft)


class RootEnforceSection(SectionSpec):
    name = "root-enforce"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return RootEnforceConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return RootEnforceConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if "." not in key:
            raise SectionError("field=key reason=invalid format hint=use section.field")
        section_name, field_name = key.split(".", 1)
        draft = deepcopy(payload)
        section = dict(draft["sections"].get(section_name, {}))
        section[field_name] = _convert_value(values)
        draft["sections"][section_name] = section
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        draft = deepcopy(payload)
        if "." in key:
            section_name, field_name = key.split(".", 1)
            section = dict(draft["sections"].get(section_name, {}))
            section.pop(field_name, None)
            if section:
                draft["sections"][section_name] = section
            else:
                draft["sections"].pop(section_name, None)
        else:
            draft["sections"].pop(key, None)
        return self.validate_payload(draft)
