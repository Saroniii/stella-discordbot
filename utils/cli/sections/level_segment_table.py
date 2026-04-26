from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import CliNode, render_cli_tree
from utils.cli.sections.base import SectionError, SelectableSectionSpec
from utils.cli.types import LevelSegmentTableConfigV1


class LevelSegmentTableSection(SelectableSectionSpec):
    name = "level-segment-table"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return LevelSegmentTableConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return LevelSegmentTableConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
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
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
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
        return ["xp"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "xp":
            return ["<xp>"]
        return []

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def build(source: dict[str, Any] | None) -> list[str]:
            root = CliNode(kind="enter", text="enter level-segment-table")
            entries = dict(source.get("entries", {})) if isinstance(source, dict) else {}
            if not entries:
                root.children.append(CliNode(kind="comment", text="# no settings"))
                return render_cli_tree([root])
            for level in sorted(entries.keys(), key=lambda item: int(item)):
                node = CliNode(kind="select", text=f"select {level}")
                node.children.append(CliNode(kind="set", text=f"set xp {entries[level]}"))
                root.children.append(node)
            return render_cli_tree([root])

        lines = ["now-config:"]
        lines.extend(build(now_config))
        lines.append("deploy-config:")
        lines.extend(build(deploy_config))
        return "\n".join(lines)
