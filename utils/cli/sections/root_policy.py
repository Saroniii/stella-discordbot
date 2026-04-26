from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import CliNode, render_cli_tree, render_config_pair, section_to_enter_path, serialize_atom, to_cli_key
from utils.cli.sections.base import SectionError, SectionSpec, SelectableSectionSpec
from utils.cli.types import RootDefaultsConfigV1, RootEnforceConfigV1, RootEnforceOverrideConfigV1


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


def _policy_storage_key(logical_section: str) -> str:
    return logical_section


def _policy_storage_field(logical_section: str, key: str) -> tuple[str, str]:
    storage_key = _policy_storage_key(logical_section)
    if storage_key == logical_section:
        return storage_key, key
    remainder = logical_section[len(storage_key) + 1 :].replace("/", ".")
    return storage_key, f"{remainder}.{key}"


def _apply_section_field_update(
    payload_sections: dict[str, dict[str, Any]],
    section_name: str,
    field_name: str,
    value: Any,
) -> dict[str, dict[str, Any]]:
    sections = dict(payload_sections)
    section = dict(sections.get(section_name, {}))
    section[field_name] = value
    sections[section_name] = section
    return sections


def _apply_section_field_unset(
    payload_sections: dict[str, dict[str, Any]],
    section_name: str,
    field_name: str,
) -> dict[str, dict[str, Any]]:
    sections = dict(payload_sections)
    section = dict(sections.get(section_name, {}))
    section.pop(field_name, None)
    if section:
        sections[section_name] = section
    else:
        sections.pop(section_name, None)
    return sections


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

    def list_set_keys(self) -> list[str]:
        return ["<section>.<field>"]

    def list_value_candidates(self, key: str) -> list[str]:
        return ["<value...>"]


class RootDefaultsControlPlaneTickSection(SectionSpec):
    name = "root-defaults/control-plane/tick"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return RootDefaultsConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return RootDefaultsConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if len(values) != 1:
            raise SectionError("field=value reason=invalid value count hint=single value only")
        canonical = key.lower()
        if canonical not in {"max-tick-limit", "overlimit-mode"}:
            raise SectionError("field=key reason=unknown key hint=allowed: max-tick-limit,overlimit-mode")

        raw = values[0]
        if canonical == "max-tick-limit":
            try:
                value = int(raw)
            except ValueError as exc:
                raise SectionError("field=max-tick-limit reason=invalid integer hint=use numeric value") from exc
        else:
            value = raw.lower()
            if value not in {"alert-only", "drop-new-work"}:
                raise SectionError("field=overlimit-mode reason=invalid value hint=alert-only|drop-new-work")

        draft = deepcopy(payload)
        sections = dict(draft.get("sections", {}))
        target = dict(sections.get("control-plane/tick", {}))
        target[canonical] = value
        sections["control-plane/tick"] = target
        draft["sections"] = sections
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        canonical = key.lower()
        if canonical not in {"max-tick-limit", "overlimit-mode"}:
            raise SectionError("field=key reason=unknown key hint=allowed: max-tick-limit,overlimit-mode")
        draft = deepcopy(payload)
        sections = dict(draft.get("sections", {}))
        target = dict(sections.get("control-plane/tick", {}))
        target.pop(canonical, None)
        if target:
            sections["control-plane/tick"] = target
        else:
            sections.pop("control-plane/tick", None)
        draft["sections"] = sections
        return self.validate_payload(draft)

    def list_set_keys(self) -> list[str]:
        return ["max-tick-limit", "overlimit-mode"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "max-tick-limit":
            return ["<100..1000000>"]
        if key == "overlimit-mode":
            return ["alert-only", "drop-new-work"]
        return []

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def extract(payload: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            sections = payload.get("sections", {})
            if not isinstance(sections, dict):
                return None
            target = sections.get("control-plane/tick", {})
            if not isinstance(target, dict):
                return None
            return {
                "max_tick_limit": target.get("max-tick-limit", target.get("max_tick_limit")),
                "overlimit_mode": target.get("overlimit-mode", target.get("overlimit_mode")),
            }

        return render_config_pair(
            self.name,
            extract(now_config),
            extract(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )


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

    def list_set_keys(self) -> list[str]:
        return ["<section>.<field>"]

    def list_value_candidates(self, key: str) -> list[str]:
        return ["<value...>"]


class RootEnforceControlPlaneTickSection(SectionSpec):
    name = "root-enforce/control-plane/tick"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return RootEnforceConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return RootEnforceConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if len(values) != 1:
            raise SectionError("field=value reason=invalid value count hint=single value only")
        canonical = key.lower()
        if canonical not in {"max-tick-limit", "overlimit-mode"}:
            raise SectionError("field=key reason=unknown key hint=allowed: max-tick-limit,overlimit-mode")

        raw = values[0]
        if canonical == "max-tick-limit":
            try:
                value = int(raw)
            except ValueError as exc:
                raise SectionError("field=max-tick-limit reason=invalid integer hint=use numeric value") from exc
        else:
            value = raw.lower()
            if value not in {"alert-only", "drop-new-work"}:
                raise SectionError("field=overlimit-mode reason=invalid value hint=alert-only|drop-new-work")

        draft = deepcopy(payload)
        sections = dict(draft.get("sections", {}))
        target = dict(sections.get("control-plane/tick", {}))
        target[canonical] = value
        sections["control-plane/tick"] = target
        draft["sections"] = sections
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        canonical = key.lower()
        if canonical not in {"max-tick-limit", "overlimit-mode"}:
            raise SectionError("field=key reason=unknown key hint=allowed: max-tick-limit,overlimit-mode")
        draft = deepcopy(payload)
        sections = dict(draft.get("sections", {}))
        target = dict(sections.get("control-plane/tick", {}))
        target.pop(canonical, None)
        if target:
            sections["control-plane/tick"] = target
        else:
            sections.pop("control-plane/tick", None)
        draft["sections"] = sections
        return self.validate_payload(draft)

    def list_set_keys(self) -> list[str]:
        return ["max-tick-limit", "overlimit-mode"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "max-tick-limit":
            return ["<100..1000000>"]
        if key == "overlimit-mode":
            return ["alert-only", "drop-new-work"]
        return []

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def extract(payload: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            sections = payload.get("sections", {})
            if not isinstance(sections, dict):
                return None
            target = sections.get("control-plane/tick", {})
            if not isinstance(target, dict):
                return None
            return {
                "max_tick_limit": target.get("max-tick-limit", target.get("max_tick_limit")),
                "overlimit_mode": target.get("overlimit-mode", target.get("overlimit_mode")),
            }

        return render_config_pair(
            self.name,
            extract(now_config),
            extract(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )


class RootEnforceOverrideSection(SelectableSectionSpec):
    name = "root-enforce-override"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return RootEnforceOverrideConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return RootEnforceOverrideConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <guild-id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <guild-id>")

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        guild_key = self._parse_selected_guild(target)
        draft = deepcopy(payload)
        guilds = dict(draft.get("guilds", {}))
        if guild_key in guilds:
            return guild_key, None
        guilds[guild_key] = {"sections": {}}
        draft["guilds"] = guilds
        return guild_key, self.validate_payload(draft)

    def list_select_candidates(self, payload: dict[str, Any]) -> list[str]:
        guilds = payload.get("guilds", {})
        if not isinstance(guilds, dict):
            return []
        return sorted(guilds.keys(), key=lambda item: int(item))

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def render(payload: dict[str, Any] | None) -> list[str]:
            root = CliNode(kind="enter", text="enter root-enforce-override")
            guilds = payload.get("guilds", {}) if isinstance(payload, dict) else {}
            if not isinstance(guilds, dict) or not guilds:
                root.children.append(CliNode(kind="comment", text="# no settings"))
                return render_cli_tree([root])
            for guild_key in sorted(guilds.keys(), key=lambda item: int(item)):
                select_node = CliNode(kind="select", text=f"select {guild_key}")
                entry = guilds.get(guild_key, {})
                sections = entry.get("sections", {}) if isinstance(entry, dict) else {}
                if isinstance(sections, dict) and sections:
                    has_setting = False
                    for section_name, fields in sorted(sections.items()):
                        if not isinstance(fields, dict):
                            continue
                        for key, value in sorted(fields.items()):
                            atom = serialize_atom(value)
                            if atom is None:
                                continue
                            has_setting = True
                            select_node.children.append(
                                CliNode(kind="set", text=f"set {to_cli_key(section_name)}.{to_cli_key(str(key))} {atom}")
                            )
                    if not has_setting:
                        select_node.children.append(CliNode(kind="comment", text="# no settings"))
                else:
                    select_node.children.append(CliNode(kind="comment", text="# no settings"))
                root.children.append(select_node)
            return render_cli_tree([root])

        return "\n".join(
            [
                "now-config:",
                *render(now_config),
                "deploy-config:",
                *render(deploy_config),
            ]
        )

    def render_show_with_context(
        self,
        now_config: dict[str, Any],
        deploy_config: dict[str, Any] | None,
        selected_object: str | None,
    ) -> str:
        selected_key = self._try_parse_selected_guild(selected_object)

        def render(payload: dict[str, Any] | None) -> list[str]:
            root = CliNode(kind="enter", text="enter root-enforce-override")
            guilds = payload.get("guilds", {}) if isinstance(payload, dict) else {}
            if not isinstance(guilds, dict) or not guilds:
                root.children.append(CliNode(kind="comment", text="# no settings"))
                return render_cli_tree([root])

            if selected_key and selected_key in guilds:
                guild_keys = [selected_key]
            elif selected_key and selected_key not in guilds:
                root.children.append(CliNode(kind="comment", text="# no settings"))
                return render_cli_tree([root])
            else:
                guild_keys = sorted(guilds.keys(), key=lambda item: int(item))

            for guild_key in guild_keys:
                select_node = CliNode(kind="select", text=f"select {guild_key}")
                entry = guilds.get(guild_key, {})
                sections = entry.get("sections", {}) if isinstance(entry, dict) else {}
                if isinstance(sections, dict) and sections:
                    has_setting = False
                    for section_name, fields in sorted(sections.items()):
                        if not isinstance(fields, dict):
                            continue
                        for key, value in sorted(fields.items()):
                            atom = serialize_atom(value)
                            if atom is None:
                                continue
                            has_setting = True
                            select_node.children.append(
                                CliNode(kind="set", text=f"set {to_cli_key(section_name)}.{to_cli_key(str(key))} {atom}")
                            )
                    if not has_setting:
                        select_node.children.append(CliNode(kind="comment", text="# no settings"))
                else:
                    select_node.children.append(CliNode(kind="comment", text="# no settings"))
                root.children.append(select_node)
            return render_cli_tree([root])

        return "\n".join(
            [
                "now-config:",
                *render(now_config),
                "deploy-config:",
                *render(deploy_config),
            ]
        )

    def _parse_selected_guild(self, selected_object: str) -> str:
        try:
            guild_id = int(selected_object)
        except ValueError as exc:
            raise SectionError("field=select reason=invalid id hint=use numeric guild id") from exc
        if guild_id <= 0:
            raise SectionError("field=select reason=invalid id hint=use positive guild id")
        return str(guild_id)

    def _try_parse_selected_guild(self, selected_object: str | None) -> str | None:
        if selected_object is None:
            return None
        try:
            return self._parse_selected_guild(selected_object)
        except SectionError:
            return None


class RootEnforceOverrideControlPlaneTickSection(SectionSpec):
    name = "root-enforce-override/control-plane/tick"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return RootEnforceOverrideConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return RootEnforceOverrideConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <guild-id>")

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        guild_key = self._parse_selected_guild(selected_object)
        if len(values) != 1:
            raise SectionError("field=value reason=invalid value count hint=single value only")
        canonical = key.lower()
        if canonical not in {"max-tick-limit", "overlimit-mode"}:
            raise SectionError("field=key reason=unknown key hint=allowed: max-tick-limit,overlimit-mode")
        raw = values[0]
        if canonical == "max-tick-limit":
            try:
                value = int(raw)
            except ValueError as exc:
                raise SectionError("field=max-tick-limit reason=invalid integer hint=use numeric value") from exc
        else:
            value = raw.lower()
            if value not in {"alert-only", "drop-new-work"}:
                raise SectionError("field=overlimit-mode reason=invalid value hint=alert-only|drop-new-work")

        draft = deepcopy(payload)
        guilds = dict(draft.get("guilds", {}))
        entry = dict(guilds.get(guild_key, {"sections": {}}))
        sections = dict(entry.get("sections", {}))
        target = dict(sections.get("control-plane/tick", {}))
        target[canonical] = value
        sections["control-plane/tick"] = target
        entry["sections"] = sections
        guilds[guild_key] = entry
        draft["guilds"] = guilds
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <guild-id>")

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        guild_key = self._parse_selected_guild(selected_object)
        canonical = key.lower()
        if canonical not in {"max-tick-limit", "overlimit-mode"}:
            raise SectionError("field=key reason=unknown key hint=allowed: max-tick-limit,overlimit-mode")
        draft = deepcopy(payload)
        guilds = dict(draft.get("guilds", {}))
        entry = dict(guilds.get(guild_key, {"sections": {}}))
        sections = dict(entry.get("sections", {}))
        target = dict(sections.get("control-plane/tick", {}))
        target.pop(canonical, None)
        if target:
            sections["control-plane/tick"] = target
        else:
            sections.pop("control-plane/tick", None)
        entry["sections"] = sections
        guilds[guild_key] = entry
        draft["guilds"] = guilds
        return self.validate_payload(draft)

    def list_set_keys(self) -> list[str]:
        return ["max-tick-limit", "overlimit-mode"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "max-tick-limit":
            return ["<100..1000000>"]
        if key == "overlimit-mode":
            return ["alert-only", "drop-new-work"]
        return []

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        return self.render_show_with_context(now_config, deploy_config, None)

    def render_show_with_context(
        self,
        now_config: dict[str, Any],
        deploy_config: dict[str, Any] | None,
        selected_object: str | None,
    ) -> str:
        selected_key = self._try_parse_selected_guild(selected_object)

        def extract(payload: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            guilds = payload.get("guilds", {})
            if not isinstance(guilds, dict) or not guilds:
                return None

            guild_key: str | None = selected_key
            if guild_key is None:
                return None
            if guild_key not in guilds:
                return None

            entry = guilds.get(guild_key, {})
            sections = entry.get("sections", {}) if isinstance(entry, dict) else {}
            target = sections.get("control-plane/tick", {}) if isinstance(sections, dict) else {}
            if not isinstance(target, dict):
                return None
            return {
                "max_tick_limit": target.get("max-tick-limit", target.get("max_tick_limit")),
                "overlimit_mode": target.get("overlimit-mode", target.get("overlimit_mode")),
            }

        return render_config_pair(
            self.name,
            extract(now_config),
            extract(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )

    def _parse_selected_guild(self, selected_object: str | None) -> str:
        if selected_object is None:
            raise SectionError("field=select reason=missing target hint=select <guild-id>")
        try:
            guild_id = int(selected_object)
        except ValueError as exc:
            raise SectionError("field=select reason=invalid id hint=use numeric guild id") from exc
        if guild_id <= 0:
            raise SectionError("field=select reason=invalid id hint=use positive guild id")
        return str(guild_id)

    def _try_parse_selected_guild(self, selected_object: str | None) -> str | None:
        if selected_object is None:
            return None
        try:
            return self._parse_selected_guild(selected_object)
        except SectionError:
            return None


class RootPolicyScopedSection(SectionSpec):
    schema_version = 1

    def __init__(self, name: str, mode: str, delegate: SectionSpec) -> None:
        self.name = name
        self.mode = mode
        self.delegate = delegate

    def default_payload(self) -> dict[str, Any]:
        if self.mode == "defaults":
            return RootDefaultsConfigV1().model_dump(mode="json")
        return RootEnforceConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            if self.mode == "defaults":
                return RootDefaultsConfigV1.model_validate(payload).model_dump(mode="json")
            return RootEnforceConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        section_name, field_name = _policy_storage_field(self._logical_section(), key)
        draft = deepcopy(payload)
        draft["sections"] = _apply_section_field_update(
            draft.get("sections", {}),
            section_name,
            field_name,
            _convert_value(values),
        )
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        section_name, field_name = _policy_storage_field(self._logical_section(), key)
        draft = deepcopy(payload)
        draft["sections"] = _apply_section_field_unset(
            draft.get("sections", {}),
            section_name,
            field_name,
        )
        return self.validate_payload(draft)

    def list_set_keys(self) -> list[str]:
        return list(self.delegate.list_set_keys())

    def list_value_candidates(self, key: str) -> list[str]:
        return list(self.delegate.list_value_candidates(key))

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        section_name = _policy_storage_key(self._logical_section())
        prefix = ""
        logical = self._logical_section()
        if section_name != logical:
            prefix = logical[len(section_name) + 1 :].replace("/", ".") + "."

        def extract(payload: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            sections = payload.get("sections", {})
            if not isinstance(sections, dict):
                return None
            fields = sections.get(section_name, {})
            if not isinstance(fields, dict):
                return None
            mapped: dict[str, Any] = {}
            if prefix:
                for key, value in fields.items():
                    if isinstance(key, str) and key.startswith(prefix):
                        mapped[key[len(prefix) :]] = value
            else:
                mapped.update(fields)
            return mapped if mapped else None

        return render_config_pair(
            self.name,
            extract(now_config),
            extract(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )

    def _logical_section(self) -> str:
        if "/" not in self.name:
            return self.name
        return self.name.split("/", 1)[1]


class RootEnforceOverrideScopedSection(SectionSpec):
    schema_version = 1

    def __init__(self, name: str, delegate: SectionSpec) -> None:
        self.name = name
        self.delegate = delegate

    def default_payload(self) -> dict[str, Any]:
        return RootEnforceOverrideConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return RootEnforceOverrideConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <guild-id>")

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        guild_key = self._parse_selected_guild(selected_object)
        section_name, field_name = _policy_storage_field(self._logical_section(), key)
        draft = deepcopy(payload)
        guilds = dict(draft.get("guilds", {}))
        entry = dict(guilds.get(guild_key, {"sections": {}}))
        sections = _apply_section_field_update(
            entry.get("sections", {}),
            section_name,
            field_name,
            _convert_value(values),
        )
        entry["sections"] = sections
        guilds[guild_key] = entry
        draft["guilds"] = guilds
        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <guild-id>")

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        guild_key = self._parse_selected_guild(selected_object)
        section_name, field_name = _policy_storage_field(self._logical_section(), key)
        draft = deepcopy(payload)
        guilds = dict(draft.get("guilds", {}))
        entry = dict(guilds.get(guild_key, {"sections": {}}))
        sections = _apply_section_field_unset(entry.get("sections", {}), section_name, field_name)
        entry["sections"] = sections
        guilds[guild_key] = entry
        draft["guilds"] = guilds
        return self.validate_payload(draft)

    def list_set_keys(self) -> list[str]:
        return list(self.delegate.list_set_keys())

    def list_value_candidates(self, key: str) -> list[str]:
        return list(self.delegate.list_value_candidates(key))

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        return self.render_show_with_context(now_config, deploy_config, None)

    def render_show_with_context(
        self,
        now_config: dict[str, Any],
        deploy_config: dict[str, Any] | None,
        selected_object: str | None,
    ) -> str:
        selected_key = self._try_parse_selected_guild(selected_object)
        section_name = _policy_storage_key(self._logical_section())
        prefix = ""
        logical = self._logical_section()
        if section_name != logical:
            prefix = logical[len(section_name) + 1 :].replace("/", ".") + "."

        def extract(payload: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            guilds = payload.get("guilds", {})
            if not isinstance(guilds, dict):
                return None
            if selected_key is None or selected_key not in guilds:
                return None
            entry = guilds.get(selected_key, {})
            sections = entry.get("sections", {}) if isinstance(entry, dict) else {}
            fields = sections.get(section_name, {}) if isinstance(sections, dict) else {}
            if not isinstance(fields, dict):
                return None
            mapped: dict[str, Any] = {}
            if prefix:
                for key, value in fields.items():
                    if isinstance(key, str) and key.startswith(prefix):
                        mapped[key[len(prefix) :]] = value
            else:
                mapped.update(fields)
            return mapped if mapped else None

        return render_config_pair(
            self.name,
            extract(now_config),
            extract(deploy_config),
            enter_path=section_to_enter_path(self.name),
        )

    def _logical_section(self) -> str:
        if "/" not in self.name:
            return self.name
        return self.name.split("/", 1)[1]

    def _parse_selected_guild(self, selected_object: str | None) -> str:
        if selected_object is None:
            raise SectionError("field=select reason=missing target hint=select <guild-id>")
        try:
            guild_id = int(selected_object)
        except ValueError as exc:
            raise SectionError("field=select reason=invalid id hint=use numeric guild id") from exc
        if guild_id <= 0:
            raise SectionError("field=select reason=invalid id hint=use positive guild id")
        return str(guild_id)

    def _try_parse_selected_guild(self, selected_object: str | None) -> str | None:
        if selected_object is None:
            return None
        try:
            return self._parse_selected_guild(selected_object)
        except SectionError:
            return None
