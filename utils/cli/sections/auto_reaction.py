from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import CliNode, quote_value, render_cli_tree
from utils.cli.sections.base import SectionError, SelectableSectionSpec
from utils.cli.types import AutoReactionConfigV1


class AutoReactionSection(SelectableSectionSpec):
    name = "auto-reaction"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return AutoReactionConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            model = AutoReactionConfigV1.model_validate(payload)
        except ValidationError as exc:
            raise self._validation_error(exc)
        rules = [rule.model_dump(mode="json") for rule in model.rules]
        ids = [int(rule["id"]) for rule in rules]
        if len(set(ids)) != len(ids):
            raise SectionError("field=id reason=duplicate id hint=id must be unique")
        return {"rules": sorted(rules, key=lambda item: int(item.get("id", 0)))}

    def validate_set_with_context(self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None) -> dict[str, Any]:
        rule_id = self._parse_selected_id(selected_object)
        if key == "channels":
            channels = self._parse_channels(values)
            return self._update_rule(payload, rule_id, "channels", channels)
        if key == "emojis":
            if not values:
                raise SectionError("field=emojis reason=empty value hint=provide one or more emojis")
            emojis = [value.strip() for value in values if value.strip()]
            if not emojis:
                raise SectionError("field=emojis reason=empty value hint=provide one or more emojis")
            return self._update_rule(payload, rule_id, "emojis", emojis)
        raise SectionError("field=key reason=unknown key hint=allowed: channels,emojis")

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        rule_id = self._parse_selected_id(selected_object)
        if key == "channels":
            return self._update_rule(payload, rule_id, "channels", [])
        if key == "emojis":
            return self._update_rule(payload, rule_id, "emojis", [])
        raise SectionError("field=key reason=unknown key hint=allowed: channels,emojis")

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def list_set_keys(self) -> list[str]:
        return ["channels", "emojis"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "channels":
            return ["<id1>, <id2> ..."]
        if key == "emojis":
            return ["😀", "🔥", "<:name:id>"]
        return []

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        target_id = self._parse_selected_id(target)
        rules = list(payload.get("rules", [])) if isinstance(payload, dict) else []
        for entry in rules:
            if int(entry.get("id", -1)) == target_id:
                return str(target_id), None
        draft = deepcopy(payload)
        current = [dict(item) for item in draft.get("rules", [])]
        current.append({"id": target_id, "channels": [], "emojis": []})
        current.sort(key=lambda item: int(item.get("id", 0)))
        draft["rules"] = current
        return str(target_id), self.validate_payload(draft)

    def list_select_candidates(self, payload: dict[str, Any]) -> list[str]:
        rules = list(payload.get("rules", [])) if isinstance(payload, dict) else []
        return [str(int(item["id"])) for item in rules if isinstance(item, dict) and "id" in item]

    def render_show_with_context(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None) -> str:
        def build(source: dict[str, Any] | None) -> list[str]:
            root = CliNode(kind="enter", text="enter auto-reaction")
            rules = list((source or {}).get("rules", []))
            if selected_object is not None:
                rules = [item for item in rules if str(item.get("id")) == selected_object]
            if not rules:
                root.children.append(CliNode(kind="comment", text="# no settings"))
                return render_cli_tree([root])
            for rule in sorted(rules, key=lambda item: int(item.get("id", 0))):
                select_node = CliNode(kind="select", text=f"select {int(rule.get('id', 0))}")
                channels = " ".join(str(v) for v in rule.get("channels", []))
                emojis = " ".join(quote_value(str(v)) for v in rule.get("emojis", []))
                select_node.children.append(CliNode(kind="set", text=f"set channels {channels if channels else '<unset>'}"))
                select_node.children.append(CliNode(kind="set", text=f"set emojis {emojis if emojis else '<unset>'}"))
                root.children.append(select_node)
            return render_cli_tree([root])

        lines = ["now-config:"]
        lines.extend(build(now_config))
        lines.append("deploy-config:")
        lines.extend(build(deploy_config))
        return "\n".join(lines)

    def _parse_selected_id(self, selected_object: str | None) -> int:
        if selected_object is None:
            raise SectionError("field=select reason=missing target hint=select <id>")
        try:
            value = int(selected_object)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric id") from exc
        if value <= 0:
            raise SectionError("field=id reason=invalid integer hint=use positive id")
        return value

    def _parse_channels(self, values: list[str]) -> list[int]:
        merged = " ".join(values).replace(",", " ")
        tokens = [item for item in merged.split() if item]
        if not tokens:
            raise SectionError("field=channels reason=empty value hint=provide one or more channel ids")
        parsed: list[int] = []
        for token in tokens:
            try:
                parsed.append(int(token))
            except ValueError as exc:
                raise SectionError("field=channels reason=invalid integer hint=use numeric channel ids") from exc
        return parsed

    def _update_rule(self, payload: dict[str, Any], target_id: int, key: str, value: Any) -> dict[str, Any]:
        draft = deepcopy(payload)
        rules = [dict(item) for item in draft.get("rules", [])]
        for idx, rule in enumerate(rules):
            if int(rule.get("id", -1)) == target_id:
                rule[key] = value
                rules[idx] = rule
                draft["rules"] = rules
                return self.validate_payload(draft)
        raise SectionError("field=id reason=not found hint=select existing id")
