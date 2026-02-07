from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.types import GuildLogConfigV1


class GuildLogSubSection(SectionSpec):
    schema_version = 1

    def __init__(self, sub_name: str) -> None:
        self.sub_name = sub_name
        self.name = f"guild-log/{sub_name}"

    def default_payload(self) -> dict[str, Any]:
        return GuildLogConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return GuildLogConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        draft = deepcopy(payload)

        if self.sub_name == "mod-log":
            if key == "channel":
                if len(values) != 1:
                    raise SectionError("field=channel reason=invalid value count hint=one channel id")
                draft["mod_log"]["channel"] = int(values[0])
            elif key == "type":
                if not values:
                    raise SectionError("field=type reason=empty value hint=provide one or more types")
                allowed = {"ban", "unban", "kick", "warn", "timeout", "mute", "unmute"}
                unknown = [value for value in values if value not in allowed]
                if unknown:
                    raise SectionError("field=type reason=invalid type hint=ban|unban|kick|warn|timeout|mute|unmute")
                draft["mod_log"]["types"] = list(dict.fromkeys(values))
            else:
                raise SectionError("field={0} reason=unknown key hint=allowed: channel,type".format(key))

        elif self.sub_name == "message-log":
            if key == "channel":
                if len(values) != 1:
                    raise SectionError("field=channel reason=invalid value count hint=one channel id")
                draft["message_log"]["channel"] = int(values[0])
            elif key == "tracking-message-count":
                if len(values) != 1:
                    raise SectionError("field=tracking-message-count reason=invalid value count hint=one integer")
                draft["message_log"]["tracking_message_count"] = int(values[0])
            elif key == "category":
                allowed = {"delete", "edit"}
                if not values:
                    raise SectionError("field=category reason=empty value hint=delete|edit")
                unknown = [value for value in values if value not in allowed]
                if unknown:
                    raise SectionError("field=category reason=invalid category hint=delete|edit")
                draft["message_log"]["categories"] = list(dict.fromkeys(values))
            else:
                raise SectionError("field={0} reason=unknown key hint=allowed: channel,tracking-message-count,category".format(key))

        elif self.sub_name == "member-log":
            if key == "channel":
                if len(values) != 1:
                    raise SectionError("field=channel reason=invalid value count hint=one channel id")
                draft["member_log"]["channel"] = int(values[0])
            elif key == "category":
                allowed = {"join", "leave", "nickname", "role", "avatar"}
                if not values:
                    raise SectionError("field=category reason=empty value hint=join|leave|nickname|role|avatar")
                unknown = [value for value in values if value not in allowed]
                if unknown:
                    raise SectionError("field=category reason=invalid category hint=join|leave|nickname|role|avatar")
                draft["member_log"]["categories"] = list(dict.fromkeys(values))
            else:
                raise SectionError("field={0} reason=unknown key hint=allowed: channel,category".format(key))

        try:
            return self.validate_payload(draft)
        except ValueError as exc:
            raise SectionError(f"field={key} reason=invalid integer hint=use numeric id") from exc

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        draft = deepcopy(payload)
        default = self.default_payload()

        if self.sub_name == "mod-log":
            if key == "channel":
                draft["mod_log"]["channel"] = default["mod_log"]["channel"]
            elif key == "type":
                draft["mod_log"]["types"] = default["mod_log"]["types"]
            else:
                raise SectionError("field={0} reason=unknown key hint=allowed: channel,type".format(key))
        elif self.sub_name == "message-log":
            if key == "channel":
                draft["message_log"]["channel"] = default["message_log"]["channel"]
            elif key == "tracking-message-count":
                draft["message_log"]["tracking_message_count"] = default["message_log"]["tracking_message_count"]
            elif key == "category":
                draft["message_log"]["categories"] = default["message_log"]["categories"]
            else:
                raise SectionError("field={0} reason=unknown key hint=allowed: channel,tracking-message-count,category".format(key))
        else:
            if key == "channel":
                draft["member_log"]["channel"] = default["member_log"]["channel"]
            elif key == "category":
                draft["member_log"]["categories"] = default["member_log"]["categories"]
            else:
                raise SectionError("field={0} reason=unknown key hint=allowed: channel,category".format(key))

        return self.validate_payload(draft)
