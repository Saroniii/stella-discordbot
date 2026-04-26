from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import CliNode, quote_value, render_cli_tree
from utils.cli.sections.base import SectionError, SelectableSectionSpec
from utils.cli.types import LevelGainPolicyConfigV1


class LevelGainPolicySection(SelectableSectionSpec):
    name = "level-gain-policy"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return {"policies": [self._default_rule(0)]}

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            model = LevelGainPolicyConfigV1.model_validate(payload)
        except ValidationError as exc:
            raise self._validation_error(exc)

        rules = [rule.model_dump(mode="json") for rule in model.policies]
        if not rules:
            raise SectionError("field=policies reason=missing default rule hint=id=0 must exist")

        ids = [int(rule["id"]) for rule in rules]
        if len(set(ids)) != len(ids):
            raise SectionError("field=id reason=duplicate id hint=id>0 must be unique")
        if 0 not in ids:
            raise SectionError("field=id reason=missing default rule hint=id=0 must exist")
        if int(rules[-1]["id"]) != 0:
            raise SectionError("field=id reason=invalid default position hint=id=0 must be last")
        for rule in rules:
            if int(rule.get("gain_range_min", 0)) > int(rule.get("gain_range_max", 0)):
                raise SectionError("field=gain-range reason=invalid range hint=min must be <= max")
            start = rule.get("time_start")
            end = rule.get("time_end")
            if (start is None) ^ (end is None):
                raise SectionError("field=time reason=invalid value hint=set both start and end")
            if start is not None and end is not None:
                normalized_start, normalized_end = _parse_time_window([str(start), str(end)])
                rule["time_start"] = normalized_start
                rule["time_end"] = normalized_end
        return {"policies": rules}

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        rule_id = self._parse_selected_id(selected_object)
        if rule_id == 0:
            raise SectionError("field=id reason=reserved rule hint=id=0 is immutable")
        if not values:
            raise SectionError(f"field={key} reason=missing value hint=provide a value")

        draft = deepcopy(payload)
        rules = list(draft.get("policies", []))
        index = self._find_rule_index(rules, rule_id)
        rule = dict(rules[index])

        if key == "name":
            rule["name"] = values[0]
        elif key == "action":
            if len(values) != 1 or values[0] not in {"deny", "gain", "override"}:
                raise SectionError("field=action reason=invalid value hint=deny|gain|override")
            rule["action"] = values[0]
        elif key == "channels":
            rule["channels"] = self._parse_scope_values(values, "channels")
        elif key == "roles":
            rule["roles"] = self._parse_scope_values(values, "roles")
        elif key == "method":
            if len(values) != 1 or values[0] not in {"message", "voice", "reaction", "any"}:
                raise SectionError("field=method reason=invalid value hint=message|voice|reaction|any")
            rule["method"] = values[0]
        elif key == "gain-mode":
            if len(values) != 1 or values[0] not in {"static", "random-range"}:
                raise SectionError("field=gain-mode reason=invalid value hint=static|random-range")
            rule["gain_mode"] = values[0]
        elif key == "gain-xp":
            if len(values) != 1:
                raise SectionError("field=gain-xp reason=invalid value count hint=use one integer")
            try:
                rule["gain_xp"] = int(values[0])
            except ValueError as exc:
                raise SectionError("field=gain-xp reason=invalid integer hint=use numeric value") from exc
        elif key == "gain-range":
            min_value, max_value = _parse_gain_range(values)
            rule["gain_range_min"] = min_value
            rule["gain_range_max"] = max_value
        elif key == "gain-time":
            if len(values) != 1:
                raise SectionError("field=gain-time reason=invalid value count hint=use one integer")
            try:
                rule["gain_time"] = int(values[0])
            except ValueError as exc:
                raise SectionError("field=gain-time reason=invalid integer hint=use numeric value") from exc
        elif key == "time":
            start, end = _parse_time_window(values)
            rule["time_start"] = start
            rule["time_end"] = end
        else:
            raise SectionError(
                "field={0} reason=unknown key hint=allowed: name,action,channels,roles,method,gain-mode,gain-xp,gain-range,gain-time,time".format(
                    key
                )
            )

        rules[index] = rule
        draft["policies"] = rules
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        rule_id = self._parse_selected_id(selected_object)
        if rule_id == 0:
            raise SectionError("field=id reason=reserved rule hint=id=0 is immutable")

        draft = deepcopy(payload)
        rules = list(draft.get("policies", []))
        index = self._find_rule_index(rules, rule_id)
        default_rule = self._default_rule(rule_id)
        rule = dict(rules[index])

        if key == "name":
            rule["name"] = default_rule["name"]
        elif key == "action":
            rule["action"] = default_rule["action"]
        elif key == "channels":
            rule["channels"] = default_rule["channels"]
        elif key == "roles":
            rule["roles"] = default_rule["roles"]
        elif key == "method":
            rule["method"] = default_rule["method"]
        elif key == "gain-mode":
            rule["gain_mode"] = default_rule["gain_mode"]
        elif key == "gain-xp":
            rule["gain_xp"] = default_rule["gain_xp"]
        elif key == "gain-range":
            rule["gain_range_min"] = default_rule["gain_range_min"]
            rule["gain_range_max"] = default_rule["gain_range_max"]
        elif key == "gain-time":
            rule["gain_time"] = default_rule["gain_time"]
        elif key == "time":
            rule["time_start"] = default_rule["time_start"]
            rule["time_end"] = default_rule["time_end"]
        else:
            raise SectionError(
                "field={0} reason=unknown key hint=allowed: name,action,channels,roles,method,gain-mode,gain-xp,gain-range,gain-time,time".format(
                    key
                )
            )

        rules[index] = rule
        draft["policies"] = rules
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def list_set_keys(self) -> list[str]:
        return ["name", "action", "channels", "roles", "method", "gain-mode", "gain-xp", "gain-range", "gain-time", "time"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "name":
            return ['"<name>"']
        if key == "action":
            return ["deny", "gain", "override"]
        if key in {"channels", "roles"}:
            return ["any", "<id...>"]
        if key == "method":
            return ["message", "voice", "reaction", "any"]
        if key == "gain-mode":
            return ["static", "random-range"]
        if key == "gain-xp":
            return ["<int>"]
        if key == "gain-range":
            return ["min", "<int>", "max", "<int>"]
        if key == "gain-time":
            return ["<seconds>"]
        if key == "time":
            return ["HH:MM", "HH:MM"]
        return []

    def insert_before(self, payload: dict[str, Any], before_id: int) -> dict[str, Any]:
        draft = deepcopy(payload)
        rules = list(draft.get("policies", []))
        self._find_rule_index(rules, before_id)
        shifted: list[dict[str, Any]] = []
        for rule in rules:
            item = dict(rule)
            if int(item["id"]) >= before_id and int(item["id"]) != 0 and before_id > 0:
                item["id"] = int(item["id"]) + 1
            shifted.append(item)
        if before_id == 0:
            next_id = max([int(item["id"]) for item in shifted if int(item["id"]) > 0], default=0) + 1
            shifted.append(self._default_rule(next_id))
        else:
            shifted.append(self._default_rule(before_id))
        shifted.sort(key=lambda item: (int(item["id"]) == 0, int(item["id"])))
        draft["policies"] = shifted
        return self.validate_payload(draft)

    def move_rule(self, payload: dict[str, Any], rule_id: int, mode: str, target_id: int | None = None) -> dict[str, Any]:
        if rule_id == 0:
            raise SectionError("field=id reason=reserved rule hint=id=0 is immutable")

        draft = deepcopy(payload)
        rules = [dict(rule) for rule in draft.get("policies", [])]
        movable = [item for item in sorted(rules, key=lambda item: int(item["id"])) if int(item["id"]) != 0]
        default_rule = next((rule for rule in rules if int(rule["id"]) == 0), None)
        if default_rule is None:
            raise SectionError("field=id reason=missing default rule hint=id=0 must exist")

        source_index = next((idx for idx, rule in enumerate(movable) if int(rule["id"]) == rule_id), None)
        if source_index is None:
            raise SectionError("field=id reason=not found hint=use show")

        moving = movable.pop(source_index)
        if mode == "top":
            insert_index = 0
        elif mode == "bottom":
            insert_index = len(movable)
        else:
            if target_id is None:
                raise SectionError("field=move reason=invalid args hint=move <id> before|after <target-id>|top|bottom")
            if target_id == 0:
                raise SectionError("field=id reason=reserved rule hint=id=0 is immutable")
            target_index = next((idx for idx, rule in enumerate(movable) if int(rule["id"]) == target_id), None)
            if target_index is None:
                raise SectionError("field=id reason=not found hint=use show")
            insert_index = target_index if mode == "before" else target_index + 1

        movable.insert(insert_index, moving)
        renumbered: list[dict[str, Any]] = []
        for index, rule in enumerate(movable, start=1):
            item = dict(rule)
            item["id"] = index
            renumbered.append(item)
        renumbered.append(dict(default_rule))
        draft["policies"] = renumbered
        return self.validate_payload(draft)

    def reorder_ids(self, payload: dict[str, Any]) -> dict[str, Any]:
        draft = deepcopy(payload)
        rules = [dict(rule) for rule in draft.get("policies", [])]
        movable = [rule for rule in sorted(rules, key=lambda item: int(item["id"])) if int(rule["id"]) != 0]
        default_rule = next((rule for rule in rules if int(rule["id"]) == 0), None)
        if default_rule is None:
            raise SectionError("field=id reason=missing default rule hint=id=0 must exist")

        normalized: list[dict[str, Any]] = []
        for index, rule in enumerate(movable, start=1):
            item = dict(rule)
            item["id"] = index
            normalized.append(item)
        normalized.append(dict(default_rule))
        draft["policies"] = normalized
        return self.validate_payload(draft)

    def _parse_selected_id(self, selected_object: str | None) -> int:
        if selected_object is None:
            raise SectionError("field=select reason=missing target hint=select <id>")
        try:
            rule_id = int(selected_object)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id") from exc
        if rule_id < 0:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id")
        return rule_id

    def select_target(self, payload: dict[str, Any], target: str) -> str:
        try:
            rule_id = int(target)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id") from exc
        if rule_id < 0:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id")
        rules = list(payload.get("policies", [])) if isinstance(payload, dict) else []
        self._find_rule_index(rules, rule_id)
        return str(rule_id)

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        try:
            rule_id = int(target)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id") from exc
        if rule_id < 0:
            raise SectionError("field=id reason=invalid integer hint=use numeric policy id")

        rules = list(payload.get("policies", [])) if isinstance(payload, dict) else []
        for rule in rules:
            if int(rule.get("id", -1)) == rule_id:
                return str(rule_id), None

        if rule_id == 0:
            raise SectionError("field=id reason=not found hint=use show")

        draft = deepcopy(payload)
        shifted: list[dict[str, Any]] = []
        for rule in list(draft.get("policies", [])):
            item = dict(rule)
            current_id = int(item.get("id", -1))
            if current_id != 0 and current_id >= rule_id:
                item["id"] = current_id + 1
            shifted.append(item)
        shifted.append(self._default_rule(rule_id))
        shifted.sort(key=lambda item: (int(item["id"]) == 0, int(item["id"])))
        draft["policies"] = shifted
        return str(rule_id), self.validate_payload(draft)

    def list_select_candidates(self, payload: dict[str, Any]) -> list[str]:
        rules = list(payload.get("policies", [])) if isinstance(payload, dict) else []
        ids = [int(rule.get("id", 0)) for rule in rules if isinstance(rule, dict) and "id" in rule]
        return [str(value) for value in sorted(ids)]

    def _find_rule_index(self, rules: list[dict[str, Any]], rule_id: int) -> int:
        for index, rule in enumerate(rules):
            if int(rule.get("id", -1)) == rule_id:
                return index
        raise SectionError("field=id reason=not found hint=use show")

    def _parse_scope_values(self, values: list[str], field_name: str) -> list[int] | str:
        if len(values) == 1 and values[0] == "any":
            return "any"
        converted: list[int] = []
        for value in values:
            try:
                converted.append(int(value))
            except ValueError as exc:
                raise SectionError(f"field={field_name} reason=invalid integer hint=use numeric id or any") from exc
        return converted

    def _default_rule(self, rule_id: int) -> dict[str, Any]:
        return {
            "id": rule_id,
            "name": "",
            "action": "gain",
            "channels": "any",
            "roles": "any",
            "method": "any",
            "gain_mode": "static",
            "gain_xp": 1,
            "gain_range_min": 1,
            "gain_range_max": 1,
            "gain_time": 10,
            "time_start": None,
            "time_end": None,
        }

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def build(source: dict[str, Any] | None) -> list[str]:
            root = CliNode(kind="enter", text="enter level-gain-policy")
            rules = list(source.get("policies", [])) if isinstance(source, dict) else []
            if not rules:
                root.children.append(CliNode(kind="comment", text="# no settings"))
                return render_cli_tree([root])
            for rule in rules:
                rule_id = int(rule.get("id", 0))
                select_node = CliNode(kind="select", text=f"select {rule_id}")
                if rule_id == 0:
                    select_node.children.append(CliNode(kind="comment", text="# immutable default rule"))
                    root.children.append(select_node)
                    continue
                name = str(rule.get("name", ""))
                if name:
                    select_node.children.append(CliNode(kind="set", text=f"set name {quote_value(name)}"))
                select_node.children.append(CliNode(kind="set", text=f"set action {rule.get('action', 'gain')}"))
                channels = rule.get("channels", "any")
                if channels == "any":
                    select_node.children.append(CliNode(kind="set", text="set channels any"))
                else:
                    select_node.children.append(CliNode(kind="set", text="set channels " + " ".join(str(value) for value in channels)))
                roles = rule.get("roles", "any")
                if roles == "any":
                    select_node.children.append(CliNode(kind="set", text="set roles any"))
                else:
                    select_node.children.append(CliNode(kind="set", text="set roles " + " ".join(str(value) for value in roles)))
                select_node.children.append(CliNode(kind="set", text=f"set method {rule.get('method', 'any')}"))
                select_node.children.append(CliNode(kind="set", text=f"set gain-mode {rule.get('gain_mode', 'static')}"))
                select_node.children.append(CliNode(kind="set", text=f"set gain-xp {rule.get('gain_xp', 1)}"))
                select_node.children.append(
                    CliNode(
                        kind="set",
                        text="set gain-range min {0} max {1}".format(
                        rule.get("gain_range_min", 1),
                        rule.get("gain_range_max", 1),
                        ),
                    )
                )
                select_node.children.append(CliNode(kind="set", text=f"set gain-time {rule.get('gain_time', 10)}"))
                if rule.get("time_start") and rule.get("time_end"):
                    select_node.children.append(CliNode(kind="set", text=f"set time {rule['time_start']} {rule['time_end']}"))
                root.children.append(select_node)
            return render_cli_tree([root])

        lines = ["now-config:"]
        lines.extend(build(now_config))
        lines.append("deploy-config:")
        lines.extend(build(deploy_config))
        return "\n".join(lines)


def _parse_gain_range(values: list[str]) -> tuple[int, int]:
    if len(values) != 4 or values[0] != "min" or values[2] != "max":
        raise SectionError("field=gain-range reason=invalid format hint=set gain-range min <n> max <n>")
    try:
        min_value = int(values[1])
        max_value = int(values[3])
    except ValueError as exc:
        raise SectionError("field=gain-range reason=invalid integer hint=use numeric value") from exc
    if min_value > max_value:
        raise SectionError("field=gain-range reason=invalid range hint=min must be <= max")
    return min_value, max_value


def _parse_time_window(values: list[str]) -> tuple[str, str]:
    if len(values) != 2:
        raise SectionError("field=time reason=invalid value count hint=set time HH:MM HH:MM")
    start = values[0]
    end = values[1]
    normalized: list[str] = []
    for token in (start, end):
        parts = token.split(":")
        if len(parts) != 2:
            raise SectionError("field=time reason=invalid format hint=use HH:MM (00:00-23:59)")
        if len(parts[0]) != 2 or len(parts[1]) != 2:
            raise SectionError("field=time reason=invalid format hint=use HH:MM (00:00-23:59)")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise SectionError("field=time reason=invalid format hint=use HH:MM (00:00-23:59)") from exc
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise SectionError("field=time reason=out of range hint=use HH:MM (00:00-23:59)")
        normalized.append(f"{hour:02d}:{minute:02d}")
    return normalized[0], normalized[1]
