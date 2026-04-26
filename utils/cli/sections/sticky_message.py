from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import CliNode, quote_value, render_cli_tree
from utils.cli.sections.base import FieldRule, MappedSectionSpec, SectionError, SectionSpec, SelectableSectionSpec
from utils.cli.types import StickyMessageConfigV1


def _parse_item_selected(selected_object: str | None) -> int:
    if selected_object is None:
        raise SectionError("field=select reason=missing target hint=select <id>")
    head = selected_object.split(":", 1)[0]
    try:
        value = int(head)
    except ValueError as exc:
        raise SectionError("field=id reason=invalid integer hint=use numeric id") from exc
    if value <= 0:
        raise SectionError("field=id reason=invalid integer hint=use positive id")
    return value


def _parse_pair_selected(selected_object: str | None, child_hint: str) -> tuple[int, int]:
    if selected_object is None or ":" not in selected_object:
        raise SectionError(f"field=select reason=missing target hint={child_hint}")
    left, right = selected_object.split(":", 1)
    if right == "":
        raise SectionError(f"field=select reason=missing target hint={child_hint}")
    try:
        item_id = int(left)
        child_id = int(right)
    except ValueError as exc:
        raise SectionError("field=id reason=invalid integer hint=use numeric id") from exc
    if item_id <= 0 or child_id <= 0:
        raise SectionError("field=id reason=invalid integer hint=use positive id")
    return item_id, child_id


def _sticky_item_rows(source: dict[str, Any] | None, target: str | None) -> list[dict[str, Any]]:
    rows = [item for item in (source or {}).get("items", []) if isinstance(item, dict)]
    if target is not None:
        rows = [item for item in rows if str(item.get("id")) == target]
    return sorted(rows, key=lambda row: int(row.get("id", 0)))


def _render_sticky_show(now_config: dict[str, Any], deploy_config: dict[str, Any] | None, target: str | None, mode: str) -> str:
    def build(source: dict[str, Any] | None) -> list[str]:
        root = CliNode(kind="enter", text="enter sticky-message")
        items = _sticky_item_rows(source, target)
        if not items:
            root.children.append(
                CliNode(kind="select", text="select <id>", children=[CliNode(kind="comment", text="# no settings")])
            )
            return render_cli_tree([root])
        root.children.extend(_build_sticky_item_node(item, mode) for item in items)
        return render_cli_tree([root])

    return "\n".join(["now-config:", *build(now_config), "deploy-config:", *build(deploy_config)])


def _build_sticky_item_node(item: dict[str, Any], mode: str) -> CliNode:
    node = CliNode(kind="select", text=f"select {int(item.get('id', 0))}")
    if mode == "full":
        node.children.extend(
            [
                CliNode(kind="set", text=f"set message {quote_value(str(item.get('message', '')))}"),
                CliNode(kind="set", text=f"set delay {int(item.get('delay', 0))}"),
                CliNode(
                    kind="set",
                    text=f"set trigger-bot-message {'enable' if bool(item.get('trigger_bot_message', False)) else 'disable'}",
                ),
            ]
        )

    if mode in {"full", "channels", "webhook"}:
        include_channel_core = mode in {"full", "channels"}
        include_webhook = mode in {"full", "webhook"}
        node.children.append(_build_sticky_channels_node(item, include_channel_core=include_channel_core, include_webhook=include_webhook))

    if mode in {"full", "embed", "fields"}:
        include_embed_values = mode in {"full", "embed"}
        include_fields = mode in {"full", "fields"}
        node.children.append(_build_sticky_embed_node(item, include_values=include_embed_values, include_fields=include_fields))

    return node


def _build_sticky_channels_node(item: dict[str, Any], *, include_channel_core: bool, include_webhook: bool) -> CliNode:
    channels_node = CliNode(kind="enter", text="enter channels")
    channels = [row for row in item.get("channels", []) if isinstance(row, dict)]
    channels = sorted(channels, key=lambda row: int(row.get("id", 0)))
    if not channels:
        channel_children: list[CliNode] = [CliNode(kind="comment", text="# no settings")]
        if include_webhook:
            channel_children = [CliNode(kind="enter", text="enter webhook", children=channel_children)]
        channels_node.children.append(CliNode(kind="select", text="select <id>", children=channel_children))
        return channels_node

    for channel in channels:
        channel_node = CliNode(kind="select", text=f"select {int(channel.get('id', 0))}")
        if include_channel_core:
            channel_id = channel.get("channel_id")
            channel_node.children.append(CliNode(kind="set", text=f"set channel-id {channel_id if channel_id is not None else '<unset>'}"))
            channel_node.children.append(CliNode(kind="set", text=f"set send-mode {str(channel.get('send_mode', 'bot'))}"))
        if include_webhook:
            webhook = channel.get("webhook", {}) if isinstance(channel.get("webhook"), dict) else {}
            webhook_node = CliNode(kind="enter", text="enter webhook")
            webhook_node.children.append(CliNode(kind="set", text=f"set name {quote_value(str(webhook.get('name', '')))}"))
            if webhook.get("icon"):
                webhook_node.children.append(CliNode(kind="set", text=f"set icon {quote_value(str(webhook.get('icon')))}"))
            if webhook.get("webhook"):
                webhook_node.children.append(CliNode(kind="set", text=f"set webhook {quote_value(str(webhook.get('webhook')))}"))
            if not webhook_node.children:
                webhook_node.children.append(CliNode(kind="comment", text="# no settings"))
            channel_node.children.append(webhook_node)
        channels_node.children.append(channel_node)
    return channels_node


def _build_sticky_embed_node(item: dict[str, Any], *, include_values: bool, include_fields: bool) -> CliNode:
    embed_node = CliNode(kind="enter", text="enter embed")
    embed = item.get("embed", {}) if isinstance(item.get("embed"), dict) else {}

    if include_values:
        for key in ["title", "description", "color", "avatar_url", "footer"]:
            value = embed.get(key)
            if value in (None, ""):
                continue
            embed_node.children.append(CliNode(kind="set", text=f"set {key.replace('_', '-')} {quote_value(str(value))}"))
        if not embed_node.children:
            embed_node.children.append(CliNode(kind="comment", text="# no settings"))

    if include_fields:
        fields_node = CliNode(kind="enter", text="enter fields")
        fields = [row for row in embed.get("fields", []) if isinstance(row, dict)] if isinstance(embed, dict) else []
        fields = sorted(fields, key=lambda row: int(row.get("id", 0)))
        if not fields:
            fields_node.children.append(CliNode(kind="select", text="select <id>", children=[CliNode(kind="comment", text="# no settings")]))
        else:
            for field in fields:
                field_node = CliNode(kind="select", text=f"select {int(field.get('id', 0))}")
                field_node.children.append(CliNode(kind="set", text=f"set name {quote_value(str(field.get('name', '')))}"))
                field_node.children.append(CliNode(kind="set", text=f"set value {quote_value(str(field.get('value', '')))}"))
                field_node.children.append(
                    CliNode(kind="set", text=f"set inline-mode {'enable' if bool(field.get('inline_mode', False)) else 'disable'}")
                )
                fields_node.children.append(field_node)
        embed_node.children.append(fields_node)

    return embed_node


class StickyMessageSection(SelectableSectionSpec):
    name = "sticky-message"
    schema_version = 2

    def default_payload(self) -> dict[str, Any]:
        return StickyMessageConfigV1().model_dump(mode="json")

    def migrate(self, version: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if version >= self.schema_version:
            return version, payload
        if version in {0, 1}:
            if "items" in payload and isinstance(payload.get("items"), list):
                return self.schema_version, payload
            legacy = {
                "message": str(payload.get("message", "")),
                "delay": int(payload.get("delay", 0) or 0),
                "trigger_bot_message": bool(payload.get("trigger_bot_message", False)),
                "channels": payload.get("channels", []),
                "embed": payload.get("embed", {}),
            }
            return self.schema_version, {"items": [{"id": 1, **legacy}]}
        raise SectionError(f"unsupported schema version: {version}")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            model = StickyMessageConfigV1.model_validate(payload)
        except ValidationError as exc:
            raise self._validation_error(exc)
        items = [item.model_dump(mode="json") for item in model.items]
        ids = [int(item["id"]) for item in items]
        if len(set(ids)) != len(ids):
            raise SectionError("field=id reason=duplicate id hint=id must be unique")
        items.sort(key=lambda item: int(item.get("id", 0)))
        return {"items": items}

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        item_id = _parse_item_selected(selected_object)
        draft = deepcopy(payload)
        item = self._find_item_mutable(draft, item_id)
        if key == "message":
            if len(values) != 1:
                raise SectionError('field=message reason=invalid value count hint=set message "<text>"')
            item["message"] = values[0]
        elif key == "delay":
            if len(values) != 1:
                raise SectionError("field=delay reason=invalid value count hint=set delay <seconds>")
            try:
                item["delay"] = int(values[0])
            except ValueError as exc:
                raise SectionError("field=delay reason=invalid integer hint=use numeric delay") from exc
        elif key == "trigger-bot-message":
            if len(values) != 1 or values[0] not in {"enable", "disable"}:
                raise SectionError("field=trigger-bot-message reason=invalid value hint=enable|disable")
            item["trigger_bot_message"] = values[0] == "enable"
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: message,delay,trigger-bot-message")
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        item_id = _parse_item_selected(selected_object)
        draft = deepcopy(payload)
        item = self._find_item_mutable(draft, item_id)
        if key == "message":
            item["message"] = ""
        elif key == "delay":
            item["delay"] = 0
        elif key == "trigger-bot-message":
            item["trigger_bot_message"] = False
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: message,delay,trigger-bot-message")
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def list_set_keys(self) -> list[str]:
        return ["message", "delay", "trigger-bot-message"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "message":
            return ['"<text>"']
        if key == "delay":
            return ["<0..3600>"]
        if key == "trigger-bot-message":
            return ["enable", "disable"]
        return []

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        try:
            target_id = int(target)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric id") from exc
        if target_id <= 0:
            raise SectionError("field=id reason=invalid integer hint=use positive id")
        items = payload.get("items", []) if isinstance(payload, dict) else []
        for item in items:
            if isinstance(item, dict) and int(item.get("id", -1)) == target_id:
                return str(target_id), None
        draft = deepcopy(payload)
        draft_items = [dict(item) for item in draft.get("items", [])]
        draft_items.append(
            {
                "id": target_id,
                "message": "",
                "delay": 0,
                "trigger_bot_message": False,
                "channels": [],
                "embed": {"title": "", "description": "", "color": None, "avatar_url": None, "footer": None, "fields": []},
            }
        )
        draft["items"] = draft_items
        return str(target_id), self.validate_payload(draft)

    def list_select_candidates(self, payload: dict[str, Any]) -> list[str]:
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return [str(int(item["id"])) for item in items if isinstance(item, dict) and "id" in item]

    def render_show_with_context(
        self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None
    ) -> str:
        target = str(_parse_item_selected(selected_object)) if selected_object else None
        return _render_sticky_show(now_config, deploy_config, target, mode="full")

    def _find_item_mutable(self, payload: dict[str, Any], item_id: int) -> dict[str, Any]:
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise SectionError("field=items reason=invalid payload hint=repair sticky-message config")
        for idx, item in enumerate(items):
            if isinstance(item, dict) and int(item.get("id", -1)) == item_id:
                mutable = dict(item)
                items[idx] = mutable
                return mutable
        raise SectionError("field=id reason=not found hint=select existing id")


class StickyChannelsSection(SelectableSectionSpec):
    name = "sticky-message/channels"
    schema_version = 2

    def default_payload(self) -> dict[str, Any]:
        return StickyMessageConfigV1().model_dump(mode="json")

    def migrate(self, version: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return StickyMessageSection().migrate(version, payload)

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return StickyMessageSection().validate_payload(payload)

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        item_id, channel_select_id = _parse_pair_selected(selected_object, "select <id>")
        draft = deepcopy(payload)
        channel = self._find_channel_mutable(draft, item_id, channel_select_id)
        if key == "channel-id":
            if len(values) != 1:
                raise SectionError("field=channel-id reason=invalid value count hint=one channel id")
            try:
                channel["channel_id"] = int(values[0])
            except ValueError as exc:
                raise SectionError("field=channel-id reason=invalid integer hint=use numeric id") from exc
        elif key == "send-mode":
            if len(values) != 1 or values[0] not in {"bot", "webhook"}:
                raise SectionError("field=send-mode reason=invalid value hint=bot|webhook")
            channel["send_mode"] = values[0]
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: channel-id,send-mode")
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        item_id, channel_select_id = _parse_pair_selected(selected_object, "select <id>")
        draft = deepcopy(payload)
        channel = self._find_channel_mutable(draft, item_id, channel_select_id)
        if key == "channel-id":
            channel["channel_id"] = None
        elif key == "send-mode":
            channel["send_mode"] = "bot"
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: channel-id,send-mode")
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def list_set_keys(self) -> list[str]:
        return ["channel-id", "send-mode"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "channel-id":
            return ["<channel-id>"]
        if key == "send-mode":
            return ["bot", "webhook"]
        return []

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        raise SectionError("field=select reason=invalid context hint=select from parent sticky-message")

    def select_target_with_payload_for_item(
        self, payload: dict[str, Any], item_id: int, target: str
    ) -> tuple[str, dict[str, Any] | None]:
        try:
            target_id = int(target)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric id") from exc
        if target_id <= 0:
            raise SectionError("field=id reason=invalid integer hint=use positive id")
        channels = self._channels_for_item(payload, item_id)
        for channel in channels:
            if int(channel.get("id", -1)) == target_id:
                return str(target_id), None
        draft = deepcopy(payload)
        item = StickyMessageSection()._find_item_mutable(draft, item_id)
        channel_rows = [dict(row) for row in item.get("channels", [])]
        channel_rows.append(
            {
                "id": target_id,
                "channel_id": None,
                "send_mode": "bot",
                "webhook": {"name": "", "icon": None, "webhook": None},
            }
        )
        channel_rows.sort(key=lambda row: int(row.get("id", 0)))
        item["channels"] = channel_rows
        return str(target_id), self.validate_payload(draft)

    def list_select_candidates_for_item(self, payload: dict[str, Any], item_id: int) -> list[str]:
        channels = self._channels_for_item(payload, item_id)
        return [str(int(row["id"])) for row in channels if isinstance(row, dict) and "id" in row]

    def render_show_with_context(
        self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None
    ) -> str:
        item_target = str(_parse_item_selected(selected_object)) if selected_object else None
        return _render_sticky_show(now_config, deploy_config, item_target, mode="channels")

    def _channels_for_item(self, payload: dict[str, Any], item_id: int) -> list[dict[str, Any]]:
        items = payload.get("items", []) if isinstance(payload, dict) else []
        for item in items:
            if isinstance(item, dict) and int(item.get("id", -1)) == item_id:
                channels = item.get("channels", [])
                return list(channels) if isinstance(channels, list) else []
        raise SectionError("field=id reason=not found hint=select existing id")

    def _find_channel_mutable(self, payload: dict[str, Any], item_id: int, channel_id: int) -> dict[str, Any]:
        item = StickyMessageSection()._find_item_mutable(payload, item_id)
        channels = item.get("channels", [])
        if not isinstance(channels, list):
            raise SectionError("field=channels reason=invalid payload hint=repair sticky-message config")
        for idx, channel in enumerate(channels):
            if isinstance(channel, dict) and int(channel.get("id", -1)) == channel_id:
                mutable = dict(channel)
                channels[idx] = mutable
                return mutable
        raise SectionError("field=id reason=not found hint=select existing id")


class StickyChannelWebhookSection(SectionSpec):
    name = "sticky-message/channels/webhook"
    schema_version = 2

    def default_payload(self) -> dict[str, Any]:
        return StickyMessageConfigV1().model_dump(mode="json")

    def migrate(self, version: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return StickyMessageSection().migrate(version, payload)

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return StickyMessageSection().validate_payload(payload)

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        item_id, channel_id = _parse_pair_selected(selected_object, "select <id>")
        if len(values) != 1:
            raise SectionError("field=value reason=invalid value count hint=single value only")
        if key not in {"name", "icon", "webhook"}:
            raise SectionError("field=key reason=unknown key hint=allowed: name,icon,webhook")
        draft = deepcopy(payload)
        channel = StickyChannelsSection()._find_channel_mutable(draft, item_id, channel_id)
        webhook = channel.get("webhook", {}) if isinstance(channel.get("webhook"), dict) else {}
        webhook[key] = values[0]
        channel["webhook"] = webhook
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        item_id, channel_id = _parse_pair_selected(selected_object, "select <id>")
        if key not in {"name", "icon", "webhook"}:
            raise SectionError("field=key reason=unknown key hint=allowed: name,icon,webhook")
        draft = deepcopy(payload)
        channel = StickyChannelsSection()._find_channel_mutable(draft, item_id, channel_id)
        webhook = channel.get("webhook", {}) if isinstance(channel.get("webhook"), dict) else {}
        webhook[key] = "" if key == "name" else None
        channel["webhook"] = webhook
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def list_set_keys(self) -> list[str]:
        return ["name", "icon", "webhook"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key == "name":
            return ['"<name>"']
        if key == "icon":
            return ["<url>"]
        if key == "webhook":
            return ["<webhook-ref-id>"]
        return []

    def render_show_with_context(
        self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None
    ) -> str:
        item_target = str(_parse_item_selected(selected_object)) if selected_object else None
        return _render_sticky_show(now_config, deploy_config, item_target, mode="webhook")


class StickyEmbedSection(MappedSectionSpec):
    name = "sticky-message/embed"
    schema_version = 2
    field_rules = {
        "title": FieldRule(path=("title",), parser=MappedSectionSpec.parse_single_string("title", 'set title "<text>"'), candidates=['"<text>"']),
        "description": FieldRule(
            path=("description",),
            parser=MappedSectionSpec.parse_single_string("description", 'set description "<text>"'),
            candidates=['"<text>"'],
        ),
        "color": FieldRule(path=("color",), parser=MappedSectionSpec.parse_single_string("color", "set color <hex|name>"), candidates=["0x5555", "blue"]),
        "avatar-url": FieldRule(path=("avatar_url",), parser=MappedSectionSpec.parse_single_string("avatar-url", "set avatar-url <url>"), candidates=["<url>"]),
        "footer": FieldRule(path=("footer",), parser=MappedSectionSpec.parse_single_string("footer", 'set footer "<text>"'), candidates=['"<text>"']),
    }

    def default_payload(self) -> dict[str, Any]:
        return StickyMessageConfigV1().model_dump(mode="json")

    def migrate(self, version: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return StickyMessageSection().migrate(version, payload)

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return StickyMessageSection().validate_payload(payload)

    def validate_set_with_context(
        self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None
    ) -> dict[str, Any]:
        item_id = _parse_item_selected(selected_object)
        canonical = self._canonical_key(key)
        rule = self.field_rules[canonical]
        draft = deepcopy(payload)
        item = StickyMessageSection()._find_item_mutable(draft, item_id)
        embed = item.get("embed", {}) if isinstance(item.get("embed"), dict) else {}
        parsed = rule.parser(values)
        self._set_path(embed, rule.path, parsed)
        item["embed"] = embed
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        item_id = _parse_item_selected(selected_object)
        canonical = self._canonical_key(key)
        rule = self.field_rules[canonical]
        draft = deepcopy(payload)
        item = StickyMessageSection()._find_item_mutable(draft, item_id)
        embed = item.get("embed", {}) if isinstance(item.get("embed"), dict) else {}
        default_value = self._get_path(StickyMessageConfigV1().model_dump(mode="json")["items"][0]["embed"], rule.path)
        self._set_path(embed, rule.path, default_value)
        item["embed"] = embed
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def render_show_with_context(
        self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None
    ) -> str:
        item_target = str(_parse_item_selected(selected_object)) if selected_object else None
        return _render_sticky_show(now_config, deploy_config, item_target, mode="embed")


class StickyEmbedFieldsSection(SelectableSectionSpec):
    name = "sticky-message/embed/fields"
    schema_version = 2

    def default_payload(self) -> dict[str, Any]:
        return StickyMessageConfigV1().model_dump(mode="json")

    def migrate(self, version: int, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return StickyMessageSection().migrate(version, payload)

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return StickyMessageSection().validate_payload(payload)

    def validate_set_with_context(self, payload: dict[str, Any], key: str, values: list[str], selected_object: str | None) -> dict[str, Any]:
        item_id, field_id = _parse_pair_selected(selected_object, "select <id>")
        if len(values) != 1:
            raise SectionError("field=value reason=invalid value count hint=single value only")
        draft = deepcopy(payload)
        field = self._find_field_mutable(draft, item_id, field_id)
        if key == "name":
            field["name"] = values[0]
        elif key == "value":
            field["value"] = values[0]
        elif key == "inline-mode":
            if values[0] not in {"enable", "disable"}:
                raise SectionError("field=inline-mode reason=invalid value hint=enable|disable")
            field["inline_mode"] = values[0] == "enable"
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: name,value,inline-mode")
        return self.validate_payload(draft)

    def apply_unset_with_context(self, payload: dict[str, Any], key: str, selected_object: str | None) -> dict[str, Any]:
        item_id, field_id = _parse_pair_selected(selected_object, "select <id>")
        draft = deepcopy(payload)
        field = self._find_field_mutable(draft, item_id, field_id)
        if key == "name":
            field["name"] = ""
        elif key == "value":
            field["value"] = ""
        elif key == "inline-mode":
            field["inline_mode"] = False
        else:
            raise SectionError("field=key reason=unknown key hint=allowed: name,value,inline-mode")
        return self.validate_payload(draft)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        raise SectionError("field=select reason=missing target hint=select <id>")

    def list_set_keys(self) -> list[str]:
        return ["name", "value", "inline-mode"]

    def list_value_candidates(self, key: str) -> list[str]:
        if key in {"name", "value"}:
            return ['"<text>"']
        if key == "inline-mode":
            return ["enable", "disable"]
        return []

    def select_target_with_payload(self, payload: dict[str, Any], target: str) -> tuple[str, dict[str, Any] | None]:
        raise SectionError("field=select reason=invalid context hint=select from sticky item")

    def select_target_with_payload_for_item(
        self, payload: dict[str, Any], item_id: int, target: str
    ) -> tuple[str, dict[str, Any] | None]:
        try:
            target_id = int(target)
        except ValueError as exc:
            raise SectionError("field=id reason=invalid integer hint=use numeric id") from exc
        if target_id <= 0:
            raise SectionError("field=id reason=invalid integer hint=use positive id")
        fields = self._fields_for_item(payload, item_id)
        for entry in fields:
            if int(entry.get("id", -1)) == target_id:
                return str(target_id), None
        draft = deepcopy(payload)
        item = StickyMessageSection()._find_item_mutable(draft, item_id)
        embed = item.get("embed", {}) if isinstance(item.get("embed"), dict) else {}
        rows = [dict(row) for row in embed.get("fields", [])]
        rows.append({"id": target_id, "name": "", "value": "", "inline_mode": False})
        rows.sort(key=lambda row: int(row.get("id", 0)))
        embed["fields"] = rows
        item["embed"] = embed
        return str(target_id), self.validate_payload(draft)

    def list_select_candidates_for_item(self, payload: dict[str, Any], item_id: int) -> list[str]:
        fields = self._fields_for_item(payload, item_id)
        return [str(int(item["id"])) for item in fields if isinstance(item, dict) and "id" in item]

    def render_show_with_context(
        self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None, selected_object: str | None
    ) -> str:
        item_target = str(_parse_item_selected(selected_object)) if selected_object else None
        return _render_sticky_show(now_config, deploy_config, item_target, mode="fields")

    def _fields_for_item(self, payload: dict[str, Any], item_id: int) -> list[dict[str, Any]]:
        items = payload.get("items", []) if isinstance(payload, dict) else []
        for item in items:
            if isinstance(item, dict) and int(item.get("id", -1)) == item_id:
                embed = item.get("embed", {})
                if isinstance(embed, dict):
                    fields = embed.get("fields", [])
                    return list(fields) if isinstance(fields, list) else []
        raise SectionError("field=id reason=not found hint=select existing id")

    def _find_field_mutable(self, payload: dict[str, Any], item_id: int, field_id: int) -> dict[str, Any]:
        item = StickyMessageSection()._find_item_mutable(payload, item_id)
        embed = item.get("embed", {}) if isinstance(item.get("embed"), dict) else {}
        fields = embed.get("fields", [])
        if not isinstance(fields, list):
            raise SectionError("field=fields reason=invalid payload hint=repair sticky-message config")
        for idx, field in enumerate(fields):
            if isinstance(field, dict) and int(field.get("id", -1)) == field_id:
                mutable = dict(field)
                fields[idx] = mutable
                return mutable
        raise SectionError("field=id reason=not found hint=select existing id")
