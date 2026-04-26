from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from utils.cli.formatter import render_config_pair, section_to_enter_path
from utils.cli.sections.base import FieldRule, MappedSectionSpec
from utils.cli.types import ChatGroupGlobalConfigV1


class ChatGroupGlobalSection(MappedSectionSpec):
    name = "chat-group-global"
    schema_version = 1
    field_rules = {
        "attachment-channel-id": FieldRule(
            path=("attachment_channel_id",),
            parser=MappedSectionSpec.parse_single_int("attachment-channel-id", hint="one channel id"),
            candidates=["<channel-id>"],
        ),
    }

    def default_payload(self) -> dict[str, Any]:
        return ChatGroupGlobalConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return ChatGroupGlobalConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def render_show(self, now_config: dict[str, Any], deploy_config: dict[str, Any] | None) -> str:
        return render_config_pair(
            self.name,
            now_config,
            deploy_config,
            enter_path=section_to_enter_path(self.name),
        )
