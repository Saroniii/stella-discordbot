from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import CliNode, payload_to_set_lines, render_cli_tree
from utils.cli.sections.base import FieldRule, MappedSectionSpec, SectionError
from utils.cli.types import GuildLogConfigV1


class GuildLogSubSection(MappedSectionSpec):
    schema_version = 1

    def __init__(self, sub_name: str) -> None:
        self.sub_name = sub_name
        self.name = f"guild-log/{sub_name}"
        self.field_aliases = {}
        if sub_name == "mod-log":
            self.field_aliases = {"category": "type"}
            self.field_rules = {
                "channel": FieldRule(
                    path=("mod_log", "channel"),
                    parser=_parse_channel_id,
                    candidates=["<channel-id>"],
                ),
                "type": FieldRule(
                    path=("mod_log", "types"),
                    parser=_parse_choice_list(
                        field="type",
                        allowed={"ban", "unban", "kick", "warn", "timeout", "mute", "unmute"},
                        empty_hint="provide one or more types",
                        invalid_hint="ban|unban|kick|warn|timeout|mute|unmute",
                    ),
                    candidates=["ban", "unban", "kick", "warn", "timeout", "mute", "unmute"],
                ),
            }
        elif sub_name == "message-log":
            self.field_rules = {
                "channel": FieldRule(
                    path=("message_log", "channel"),
                    parser=_parse_channel_id,
                    candidates=["<channel-id>"],
                ),
                "tracking-message-count": FieldRule(
                    path=("message_log", "tracking_message_count"),
                    parser=MappedSectionSpec.parse_single_int("tracking-message-count", "one integer"),
                    candidates=["<100..100000>"],
                ),
                "tracking-message-mode": FieldRule(
                    path=("message_log", "tracking_message_mode"),
                    parser=MappedSectionSpec.parse_single_choice(
                        "tracking-message-mode",
                        choices={"normal", "extra"},
                        hint="normal|extra",
                    ),
                    candidates=["normal", "extra"],
                ),
                "category": FieldRule(
                    path=("message_log", "categories"),
                    parser=_parse_choice_list(
                        field="category",
                        allowed={"delete", "edit"},
                        empty_hint="delete|edit",
                        invalid_hint="delete|edit",
                    ),
                    candidates=["delete", "edit"],
                ),
            }
        else:
            self.field_rules = {
                "channel": FieldRule(
                    path=("member_log", "channel"),
                    parser=_parse_channel_id,
                    candidates=["<channel-id>"],
                ),
                "category": FieldRule(
                    path=("member_log", "categories"),
                    parser=_parse_choice_list(
                        field="category",
                        allowed={"join", "leave", "nickname", "role", "avatar"},
                        empty_hint="join|leave|nickname|role|avatar",
                        invalid_hint="join|leave|nickname|role|avatar",
                    ),
                    candidates=["join", "leave", "nickname", "role", "avatar"],
                ),
            }

    def default_payload(self) -> dict[str, Any]:
        return GuildLogConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return GuildLogConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        def build_lines(source: dict[str, Any] | None) -> list[str]:
            root = CliNode(kind="enter", text="enter guild-log")
            sub = CliNode(kind="enter", text=f"enter {self.sub_name}")
            root.children.append(sub)
            if not source:
                sub.children.append(CliNode(kind="comment", text="# no settings"))
                return render_cli_tree([root])
            key_map = {"mod-log": "mod_log", "message-log": "message_log", "member-log": "member_log"}
            if self.sub_name == "mod-log" and any(key in source for key in {"channel", "types"}):
                sub_payload = source
            elif self.sub_name == "message-log" and any(
                key in source for key in {"channel", "tracking_message_count", "tracking_message_mode", "categories"}
            ):
                sub_payload = source
            elif self.sub_name == "member-log" and any(key in source for key in {"channel", "categories"}):
                sub_payload = source
            else:
                sub_payload = source.get(key_map[self.sub_name], {})
            if not isinstance(sub_payload, dict):
                sub.children.append(CliNode(kind="comment", text="# no settings"))
                return render_cli_tree([root])
            cli_payload = dict(sub_payload)
            if self.sub_name == "mod-log" and "types" in cli_payload:
                cli_payload["type"] = cli_payload.pop("types")
            if self.sub_name in {"message-log", "member-log"} and "categories" in cli_payload:
                cli_payload["category"] = cli_payload.pop("categories")
            set_lines = payload_to_set_lines(cli_payload)
            if not set_lines:
                sub.children.append(CliNode(kind="comment", text="# no settings"))
            else:
                sub.children.extend(CliNode(kind="set", text=line) for line in set_lines)
            return render_cli_tree([root])

        lines = ["now-config:"]
        lines.extend(build_lines(now_config))
        lines.append("deploy-config:")
        lines.extend(build_lines(deploy_config))
        return "\n".join(lines)


def _parse_channel_id(values: list[str]) -> int:
    if len(values) != 1:
        raise SectionError("field=channel reason=invalid value count hint=one channel id")
    try:
        return int(values[0])
    except ValueError as exc:
        raise SectionError("field=channel reason=invalid integer hint=use numeric id") from exc


def _parse_choice_list(field: str, allowed: set[str], empty_hint: str, invalid_hint: str):
    def parser(values: list[str]) -> list[str]:
        if not values:
            raise SectionError(f"field={field} reason=empty value hint={empty_hint}")
        unknown = [value for value in values if value not in allowed]
        if unknown:
            raise SectionError(f"field={field} reason=invalid value hint={invalid_hint}")
        return list(dict.fromkeys(values))

    return parser
