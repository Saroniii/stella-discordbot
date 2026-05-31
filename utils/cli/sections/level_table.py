from __future__ import annotations

from copy import deepcopy
from typing import Any, ClassVar

from pydantic import BaseModel

from utils.cli.formatter import CliNode, render_config_pair_from_builders
from utils.cli.sections.base import PydanticSectionSpec, SectionError, SelectableSectionSpec


class LevelXpTableSection(PydanticSectionSpec, SelectableSectionSpec):
    model_type: ClassVar[type[BaseModel]]
    xp_candidate = "<xp>"

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        if key == "max-entries":
            return self._set_max_entries(payload, values)
        if selected_object is None:
            raise SectionError("field=select reason=missing target hint=select <id>")
        if key != "xp":
            raise SectionError("field={0} reason=unknown key hint=allowed: xp".format(key))
        if len(values) != 1:
            raise SectionError("field=xp reason=invalid value count hint=use one integer")
        try:
            level = int(selected_object)
            xp = int(values[0])
        except ValueError as exc:
            raise SectionError("field=xp reason=invalid integer hint=use numeric value") from exc
        if level <= 0:
            raise SectionError("field=id reason=invalid integer hint=select > 0")
        draft = deepcopy(payload)
        entries = dict(draft.get("entries", {}))
        max_entries = int(draft.get("max_entries", 0) or 0)
        is_new_entry = str(level) not in entries
        if max_entries > 0 and is_new_entry and len(entries) >= max_entries:
            raise SectionError(f"field=entries reason=too many values hint=max {max_entries}")
        entries[str(level)] = xp
        draft["entries"] = entries
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        if selected_object is None:
            raise SectionError("field=select reason=missing target hint=select <id>")
        if key != "xp":
            raise SectionError("field={0} reason=unknown key hint=allowed: xp".format(key))
        try:
            level = int(selected_object)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id") from exc
        draft = deepcopy(payload)
        entries = dict(draft.get("entries", {}))
        entries.pop(str(level), None)
        draft["entries"] = entries
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        if key == "max-entries":
            return self._set_max_entries(payload, values)
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        if key == "max-entries":
            draft = deepcopy(payload)
            draft["max_entries"] = 0
            return self.validate_payload(draft)
        raise SectionError("field=select reason=missing target hint=select <id>")

    def select_target(self, payload: dict[str, Any], target: str) -> str:
        try:
            level = int(target)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=select > 0") from exc
        if level <= 0:
            raise SectionError("field=id reason=invalid integer hint=select > 0")
        return str(level)

    def list_select_candidates(self, payload: dict[str, Any]) -> list[str]:
        entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
        if not isinstance(entries, dict):
            return ["<id>"]
        return sorted(entries.keys(), key=lambda item: int(item))

    def list_set_keys(self) -> list[str]:
        return ["xp", "max-entries"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "xp":
            return [self.xp_candidate]
        if key == "max-entries":
            return ["0", "100", "1000"]
        return []

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def build(source: dict[str, Any] | None) -> CliNode:
            root = CliNode(kind="enter", text=f"enter {self.name}")
            root.children.append(CliNode(kind="set", text=f"set max-entries {int(source.get('max_entries', 0) or 0) if isinstance(source, dict) else 0}"))
            entries = dict(source.get("entries", {})) if isinstance(source, dict) else {}
            if not entries:
                return root
            for level in sorted(entries.keys(), key=lambda item: int(item)):
                node = CliNode(kind="select", text=f"select {level}")
                node.children.append(CliNode(kind="set", text=f"set xp {entries[level]}"))
                root.children.append(node)
            return root

        return render_config_pair_from_builders(now_config, deploy_config, build)

    def _set_max_entries(self, payload: dict[str, Any], values: list[str]) -> dict[str, Any]:
        if len(values) != 1:
            raise SectionError("field=max-entries reason=invalid value count hint=one integer, 0 means unlimited")
        try:
            value = int(values[0])
        except ValueError as exc:
            raise SectionError("field=max-entries reason=invalid integer hint=use numeric value") from exc
        if value < 0:
            raise SectionError("field=max-entries reason=invalid value hint=use >= 0")
        entries = dict(payload.get("entries", {}))
        if value > 0 and len(entries) > value:
            raise SectionError(f"field=max-entries reason=too small hint=current entries={len(entries)}")
        draft = deepcopy(payload)
        draft["max_entries"] = value
        return self.validate_payload(draft)
