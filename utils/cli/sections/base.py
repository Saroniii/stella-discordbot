from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import ValidationError

from utils.cli.formatter import render_config_pair, section_to_enter_path


class SectionError(Exception):
    pass


@dataclass(frozen=True)
class FieldRule:
    path: tuple[str, ...]
    parser: Callable[[list[str]], Any]
    candidates: list[str]


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

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        return self.validate_set(payload, key, values)

    @abstractmethod
    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise NotImplementedError

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        return self.apply_unset(payload, key)

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        return render_config_pair(self.name, now_config, deploy_config, enter_path=section_to_enter_path(self.name))

    def render_show_with_context(
        self,
        now_config: dict[str, Any],
        deploy_config: dict[str, Any] | None,
        selected_object: str | None,
    ) -> str:
        return self.render_show(now_config, deploy_config)

    def handle_get(self, target: str) -> str:
        return f"get not supported for section={self.name} target={target}"

    def handle_diagnose(self) -> str:
        return f"section={self.name} schema_version={self.schema_version}"

    def list_set_keys(self) -> list[str]:
        return []

    def list_value_candidates(self, key: str) -> list[str]:
        return []

    def supports_select(self) -> bool:
        return False

    def select_delegate_section(self) -> str | None:
        return None

    def select_target(self, payload: dict[str, Any], target: str) -> str:
        raise SectionError("field=select reason=invalid context hint=select not supported in this section")

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        return self.select_target(payload, target), None

    def list_select_candidates(self, payload: dict[str, Any]) -> list[str]:
        return []

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


class MappedSectionSpec(SectionSpec):
    field_rules: dict[str, FieldRule] = {}
    field_aliases: dict[str, str] = {}

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        canonical = self._canonical_key(key)
        rule = self.field_rules[canonical]
        draft = self._copy_payload(payload)
        parsed = rule.parser(values)
        self._set_path(draft, rule.path, parsed)
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        canonical = self._canonical_key(key)
        rule = self.field_rules[canonical]
        draft = self._copy_payload(payload)
        default_payload = self.default_payload()
        default_value = self._get_path(default_payload, rule.path)
        self._set_path(draft, rule.path, default_value)
        return self.validate_payload(draft)

    def list_set_keys(self) -> list[str]:
        return list(self.field_rules.keys())

    def list_value_candidates(self, key: str) -> list[str]:
        canonical = self.field_aliases.get(key, key)
        rule = self.field_rules.get(canonical)
        if not rule:
            return []
        return list(rule.candidates)

    def _canonical_key(self, key: str) -> str:
        canonical = self.field_aliases.get(key, key)
        if canonical not in self.field_rules:
            hint = ",".join(self.field_rules.keys())
            raise SectionError(f"field={key} reason=unknown key hint=allowed: {hint}")
        return canonical

    def _copy_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        copied: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                copied[key] = self._copy_payload(value)
            elif isinstance(value, list):
                copied[key] = list(value)
            else:
                copied[key] = value
        return copied

    def _get_path(self, payload: dict[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = payload
        for part in path:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        if isinstance(current, list):
            return list(current)
        if isinstance(current, dict):
            return self._copy_payload(current)
        return current

    def _set_path(self, payload: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
        current = payload
        for part in path[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                current[part] = next_value
            current = next_value
        leaf = path[-1]
        if isinstance(value, list):
            current[leaf] = list(value)
        elif isinstance(value, dict):
            current[leaf] = self._copy_payload(value)
        else:
            current[leaf] = value

    @staticmethod
    def parse_enable_disable(field: str) -> Callable[[list[str]], bool]:
        def parser(values: list[str]) -> bool:
            if len(values) != 1:
                raise SectionError(f"field={field} reason=invalid value count hint=enable|disable")
            value = values[0].lower()
            if value not in {"enable", "disable"}:
                raise SectionError(f"field={field} reason=invalid value hint=enable|disable")
            return value == "enable"

        return parser

    @staticmethod
    def parse_single_int(field: str, hint: str) -> Callable[[list[str]], int]:
        def parser(values: list[str]) -> int:
            if len(values) != 1:
                raise SectionError(f"field={field} reason=invalid value count hint={hint}")
            try:
                return int(values[0])
            except ValueError as exc:
                raise SectionError(f"field={field} reason=invalid integer hint=use numeric value") from exc

        return parser

    @staticmethod
    def parse_nonempty_int_list(field: str, empty_hint: str, invalid_hint: str) -> Callable[[list[str]], list[int]]:
        def parser(values: list[str]) -> list[int]:
            if not values:
                raise SectionError(f"field={field} reason=empty value hint={empty_hint}")
            try:
                return [int(value) for value in values]
            except ValueError as exc:
                raise SectionError(f"field={field} reason=invalid integer hint={invalid_hint}") from exc

        return parser

    @staticmethod
    def parse_single_choice(field: str, choices: set[str], hint: str) -> Callable[[list[str]], str]:
        def parser(values: list[str]) -> str:
            if len(values) != 1:
                raise SectionError(f"field={field} reason=invalid value count hint={hint}")
            value = values[0].lower()
            if value not in choices:
                raise SectionError(f"field={field} reason=invalid value hint={hint}")
            return value

        return parser

    @staticmethod
    def parse_single_string(field: str, hint: str) -> Callable[[list[str]], str]:
        def parser(values: list[str]) -> str:
            if len(values) != 1:
                raise SectionError(f"field={field} reason=invalid value count hint={hint}")
            return values[0]

        return parser


class SelectableSectionSpec(SectionSpec):
    def supports_select(self) -> bool:
        return True
