from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import CliNode, quote_value, render_cli_tree
from utils.cli.sections.base import MappedSectionSpec, SectionError, SelectableSectionSpec
from utils.cli.types import ChatGroupConfigV1


def _parse_int(value: str, field: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise SectionError(f"field={field} reason=invalid integer hint=use numeric id") from exc
    if number <= 0:
        raise SectionError(f"field={field} reason=invalid integer hint=use positive id")
    return number


def _parse_selected(selected_object: str | None) -> int:
    if selected_object is None:
        raise SectionError("field=select reason=missing target hint=select <id>")
    head = selected_object.split(":", 1)[0]
    return _parse_int(head, "id")


def _parse_pair_selected(selected_object: str | None) -> tuple[int, int]:
    if selected_object is None or ":" not in selected_object:
        raise SectionError("field=select reason=missing target hint=select <id>")
    left, right = selected_object.split(":", 1)
    if right == "":
        raise SectionError("field=select reason=missing target hint=select <id>")
    return _parse_int(left, "id"), _parse_int(right, "id")


def _group_rows(payload: dict[str, Any] | None, target: str | None) -> list[dict[str, Any]]:
    rows = [row for row in (payload or {}).get("groups", []) if isinstance(row, dict)]
    if target is not None:
        rows = [row for row in rows if str(row.get("id")) == target]
    return sorted(rows, key=lambda row: int(row.get("id", 0)))


def _render_chat_group_show(now_config: dict[str, Any], deploy_config: dict[str, Any] | None, target: str | None, mode: str) -> str:
    def build(source: dict[str, Any] | None) -> list[str]:
        root = CliNode(kind="enter", text="enter chat-group")
        rows = _group_rows(source, target)
        if not rows:
            root.children.append(
                CliNode(kind="select", text="select <id>", children=[CliNode(kind="comment", text="# no settings")])
            )
            return render_cli_tree([root])
        root.children.extend(_build_chat_group_item_node(row, mode) for row in rows)
        return render_cli_tree([root])

    return "\n".join(["now-config:", *build(now_config), "deploy-config:", *build(deploy_config)])


def _build_chat_group_item_node(item: dict[str, Any], mode: str) -> CliNode:
    node = CliNode(kind="select", text=f"select {int(item.get('id', 0))}")
    if mode == "full":
        node.children.append(CliNode(kind="set", text=f"set name {quote_value(str(item.get('name', '')))}"))
        group_id = str(item.get("group_id", ""))
        if group_id:
            node.children.append(CliNode(kind="set", text=f"set group-id {quote_value(group_id)}"))
        node.children.append(CliNode(kind="set", text=f"set mode {str(item.get('mode', 'public'))}"))
        node.children.append(
            CliNode(kind="set", text=f"set join-need-apply {'enable' if bool(item.get('join_need_apply', False)) else 'disable'}")
        )
        node.children.append(CliNode(kind="set", text=f"set status {str(item.get('status', 'active'))}"))

    if mode in {"full", "connection"}:
        conn = item.get("connection", {}) if isinstance(item.get("connection"), dict) else {}
        conn_node = CliNode(kind="enter", text="enter connection")
        channel = conn.get("channel")
        conn_node.children.append(CliNode(kind="set", text=f"set channel {channel if channel is not None else '<unset>'}"))
        webhook = conn.get("webhook")
        if webhook:
            conn_node.children.append(CliNode(kind="set", text=f"set webhook {quote_value(str(webhook))}"))
        conn_node.children.append(CliNode(kind="set", text=f"set name-format {quote_value(str(conn.get('name_format', '{nickname} / {guild_name}')))}"))
        node.children.append(conn_node)

    if mode in {"full", "member-guilds"}:
        members_node = CliNode(kind="enter", text="enter member-guilds")
        members = [row for row in item.get("member_guilds", []) if isinstance(row, dict)]
        members = sorted(members, key=lambda row: int(row.get("id", 0)))
        if not members:
            members_node.children.append(
                CliNode(kind="select", text="select <id>", children=[CliNode(kind="comment", text="# no settings")])
            )
        else:
            for member in members:
                child = CliNode(kind="select", text=f"select {int(member.get('id', 0))}")
                child.children.append(CliNode(kind="set", text=f"set guild {int(member.get('guild', 0))}"))
                child.children.append(CliNode(kind="set", text=f"set status {str(member.get('status', 'active'))}"))
                child.children.append(CliNode(kind="set", text=f"set role {str(member.get('role', 'normal'))}"))
                members_node.children.append(child)
        node.children.append(members_node)

    return node


class ChatGroupSection(SelectableSectionSpec):
    name = "chat-group"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return ChatGroupConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            model = ChatGroupConfigV1.model_validate(payload)
        except ValidationError as exc:
            raise self._validation_error(exc)
        rows = [row.model_dump(mode="json") for row in model.groups]
        ids = [int(row["id"]) for row in rows]
        if len(ids) != len(set(ids)):
            raise SectionError("field=id reason=duplicate id hint=id must be unique")
        rows.sort(key=lambda row: int(row.get("id", 0)))
        for row in rows:
            members = [m for m in row.get("member_guilds", []) if isinstance(m, dict)]
            member_ids = [int(m.get("id", 0)) for m in members]
            if len(member_ids) != len(set(member_ids)):
                raise SectionError("field=id reason=duplicate id hint=id must be unique")
            members.sort(key=lambda m: int(m.get("id", 0)))
            row["member_guilds"] = members
        return {"groups": rows}

    def list_set_keys(self) -> list[str]:
        return ["name", "group-id", "mode", "join-need-apply", "status"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "group-id":
            return ["<read-only>"]
        if key == "mode":
            return ["discovery", "public", "private"]
        if key == "join-need-apply":
            return ["enable", "disable"]
        if key == "status":
            return ["active", "disable"]
        return []

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def validate_set_with_context(self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None) -> dict[str, Any]:
        group_id = _parse_selected(selected_object)
        draft = deepcopy(payload)
        item = self._find_group_mutable(draft, group_id)
        if key == "name":
            if len(values) != 1:
                raise SectionError('field=name reason=invalid value count hint=set name "<text>"')
            item["name"] = values[0]
        elif key == "group-id":
            raise SectionError("field=group-id reason=readonly hint=managed by execute chat-group create")
        elif key == "mode":
            if len(values) != 1 or values[0] not in {"discovery", "public", "private"}:
                raise SectionError("field=mode reason=invalid value hint=discovery|public|private")
            item["mode"] = values[0]
        elif key == "join-need-apply":
            if len(values) != 1 or values[0] not in {"enable", "disable"}:
                raise SectionError("field=join-need-apply reason=invalid value hint=enable|disable")
            item["join_need_apply"] = values[0] == "enable"
        elif key == "status":
            if len(values) != 1 or values[0] not in {"active", "disable"}:
                raise SectionError("field=status reason=invalid value hint=active|disable")
            item["status"] = values[0]
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: name,group-id,mode,join-need-apply,status")
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        group_id = _parse_selected(selected_object)
        draft = deepcopy(payload)
        item = self._find_group_mutable(draft, group_id)
        defaults = {
            "name": "",
            "mode": "public",
            "join-need-apply": False,
            "status": "active",
        }
        if key == "group-id":
            raise SectionError("field=group-id reason=readonly hint=managed by execute chat-group create")
        if key not in defaults:
            raise SectionError("field=key reason=unknown key hint=allowed: name,group-id,mode,join-need-apply,status")
        mapped = {
            "join-need-apply": "join_need_apply",
        }.get(key, key.replace("-", "_"))
        item[mapped] = defaults[key]
        return self.validate_payload(draft)

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        selected = _parse_int(target, "id")
        rows = payload.get("groups", [])
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and int(row.get("id", 0)) == selected:
                    return str(selected), None
        draft = deepcopy(payload)
        draft.setdefault("groups", [])
        draft["groups"].append(
            {
                "id": selected,
                "name": "",
                "group_id": "",
                "mode": "public",
                "join_need_apply": False,
                "status": "active",
                "connection": {"channel": None, "webhook": None, "name_format": "{nickname} / {guild_name}"},
                "member_guilds": [],
            }
        )
        return str(selected), self.validate_payload(draft)

    def list_select_candidates(self, payload: dict[str, Any]) -> list[str]:
        return [str(int(row.get("id", 0))) for row in payload.get("groups", []) if isinstance(row, dict)]

    def render_show_with_context(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None) -> str:
        target = selected_object.split(":", 1)[0] if selected_object else None
        return _render_chat_group_show(now_config, deploy_config, target, mode="full")

    def _find_group_mutable(self, payload: dict[str, Any], group_id: int) -> dict[str, Any]:
        rows = payload.get("groups", [])
        if not isinstance(rows, list):
            raise SectionError("field=groups reason=invalid payload hint=repair chat-group config")
        for row in rows:
            if isinstance(row, dict) and int(row.get("id", 0)) == int(group_id):
                return row
        raise SectionError("field=id reason=not found hint=select existing id")


class ChatGroupConnectionSection(MappedSectionSpec):
    name = "chat-group/connection"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return ChatGroupConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return ChatGroupSection().validate_payload(payload)

    def list_set_keys(self) -> list[str]:
        return ["channel", "webhook", "name-format"]

    def validate_set_with_context(self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None) -> dict[str, Any]:
        group_id = _parse_selected(selected_object)
        draft = deepcopy(payload)
        item = ChatGroupSection()._find_group_mutable(draft, group_id)
        conn = item.setdefault("connection", {})
        if key == "channel":
            if len(values) != 1:
                raise SectionError("field=channel reason=invalid value count hint=set channel <id>")
            conn["channel"] = _parse_int(values[0], "channel")
        elif key == "webhook":
            if len(values) != 1:
                raise SectionError("field=webhook reason=invalid value count hint=set webhook <webhook-id>")
            conn["webhook"] = values[0]
        elif key == "name-format":
            if len(values) != 1:
                raise SectionError('field=name-format reason=invalid value count hint=set name-format "..."')
            conn["name_format"] = values[0]
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: channel,webhook,name-format")
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        group_id = _parse_selected(selected_object)
        draft = deepcopy(payload)
        item = ChatGroupSection()._find_group_mutable(draft, group_id)
        conn = item.setdefault("connection", {})
        defaults = {
            "channel": None,
            "webhook": None,
            "name-format": "{nickname} / {guild_name}",
        }
        if key not in defaults:
            raise SectionError("field=key reason=unknown key hint=allowed: channel,webhook,name-format")
        mapped = "name_format" if key == "name-format" else key
        conn[mapped] = defaults[key]
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id> in chat-group")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id> in chat-group")

    def render_show_with_context(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None) -> str:
        target = selected_object.split(":", 1)[0] if selected_object else None
        return _render_chat_group_show(now_config, deploy_config, target, mode="connection")


class ChatGroupMemberGuildsSection(SelectableSectionSpec):
    name = "chat-group/member-guilds"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return ChatGroupConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return ChatGroupSection().validate_payload(payload)

    def list_set_keys(self) -> list[str]:
        return ["guild", "status", "role"]

    def select_target_with_payload_for_item(self, payload: dict[str, Any], item_id: int, target: str) -> tuple[str, dict[str, Any] | None]:
        selected = _parse_int(target, "id")
        item = ChatGroupSection()._find_group_mutable(payload, item_id)
        members = item.get("member_guilds", [])
        if isinstance(members, list):
            for row in members:
                if isinstance(row, dict) and int(row.get("id", 0)) == selected:
                    return str(selected), None
        draft = deepcopy(payload)
        item_mut = ChatGroupSection()._find_group_mutable(draft, item_id)
        item_mut.setdefault("member_guilds", []).append(
            {
                "id": selected,
                "guild": 0,
                "status": "active",
                "role": "normal",
            }
        )
        return str(selected), self.validate_payload(draft)

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        raise SectionError("field=select reason=invalid context hint=select from parent chat-group")

    def validate_set_with_context(self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None) -> dict[str, Any]:
        item_id, member_id = _parse_pair_selected(selected_object)
        draft = deepcopy(payload)
        member = self._find_member_mutable(draft, item_id, member_id)
        if key == "guild":
            if len(values) != 1:
                raise SectionError("field=guild reason=invalid value count hint=set guild <guild-id>")
            member["guild"] = _parse_int(values[0], "guild")
        elif key == "status":
            if len(values) != 1 or values[0] not in {"active", "pending", "disable"}:
                raise SectionError("field=status reason=invalid value hint=active|pending|disable")
            member["status"] = values[0]
        elif key == "role":
            if len(values) != 1 or values[0] not in {"leader", "manager", "normal"}:
                raise SectionError("field=role reason=invalid value hint=leader|manager|normal")
            member["role"] = values[0]
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: guild,status,role")
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        item_id, member_id = _parse_pair_selected(selected_object)
        draft = deepcopy(payload)
        member = self._find_member_mutable(draft, item_id, member_id)
        defaults = {
            "guild": 0,
            "status": "active",
            "role": "normal",
        }
        if key not in defaults:
            raise SectionError("field=key reason=unknown key hint=allowed: guild,status,role")
        member[key] = defaults[key]
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def list_select_candidates(self, payload: dict[str, Any]) -> list[str]:
        return []

    def render_show_with_context(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None) -> str:
        target = selected_object.split(":", 1)[0] if selected_object else None
        return _render_chat_group_show(now_config, deploy_config, target, mode="member-guilds")

    def _find_member_mutable(self, payload: dict[str, Any], item_id: int, member_id: int) -> dict[str, Any]:
        item = ChatGroupSection()._find_group_mutable(payload, item_id)
        members = item.get("member_guilds", [])
        if not isinstance(members, list):
            raise SectionError("field=member-guilds reason=invalid payload hint=repair chat-group config")
        for row in members:
            if isinstance(row, dict) and int(row.get("id", 0)) == member_id:
                return row
        raise SectionError("field=id reason=not found hint=select existing id")
