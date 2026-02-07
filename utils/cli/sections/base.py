from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import ValidationError


class SectionError(Exception):
    pass


class SectionSpec(ABC):
    name: str
    schema_version: int = 1

    @abstractmethod
    def default_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise NotImplementedError

    def render_show(self, committed: dict[str, Any], candidate: dict[str, Any] | None) -> str:
        lines = ["committed:", str(committed)]
        if candidate is not None:
            lines.extend(["candidate:", str(candidate)])
        return "\n".join(lines)

    def handle_get(self, target: str) -> str:
        return f"get not supported for section={self.name} target={target}"

    def handle_diagnose(self) -> str:
        return f"section={self.name} schema_version={self.schema_version}"

    def migrate(self, version: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if version == self.schema_version:
            return version, payload
        if version == 0:
            return self.schema_version, payload
        raise SectionError(f"unsupported schema version: {version}")

    def _validation_error(self, exc: ValidationError, field: str | None = None) -> SectionError:
        first = exc.errors()[0]
        loc = ".".join(str(item) for item in first.get("loc", []))
        message = first.get("msg", "invalid value")
        hint_field = field or loc or "unknown"
        return SectionError(f"field={hint_field} reason={message} hint=check type and allowed values")
