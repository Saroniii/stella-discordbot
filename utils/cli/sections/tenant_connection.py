from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from utils.cli.sections.base import SectionError, SectionSpec
from utils.cli.types import TenantConnectionConfigV1


class TenantConnectionLogSection(SectionSpec):
    name = "tenant-connection/log"
    schema_version = 1

    def default_payload(self) -> dict[str, Any]:
        return TenantConnectionConfigV1().model_dump(mode="json")

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return TenantConnectionConfigV1.model_validate(payload).model_dump(mode="json")
        except ValidationError as exc:
            raise self._validation_error(exc)

    def validate_set(self, payload: dict[str, Any], key: str, values: list[str]) -> dict[str, Any]:
        canonical_key = "receive-mode" if key == "recive-mode" else key
        draft = deepcopy(payload)

        if canonical_key == "crashlog-report-channel":
            if len(values) != 1:
                raise SectionError("field=crashlog-report-channel reason=invalid value count hint=one channel id")
            try:
                draft["log"]["crashlog_report_channel"] = int(values[0])
            except ValueError as exc:
                raise SectionError("field=crashlog-report-channel reason=invalid integer hint=use numeric channel id") from exc
        elif canonical_key == "receive-mode":
            if len(values) != 1:
                raise SectionError("field=receive-mode reason=invalid value count hint=off|discord|database|both")
            value = values[0].lower()
            if value not in {"off", "discord", "database", "both"}:
                raise SectionError("field=receive-mode reason=invalid value hint=off|discord|database|both")
            draft["log"]["receive_mode"] = value
        elif canonical_key == "crashlog-max-buffer":
            if len(values) != 1:
                raise SectionError("field=crashlog-max-buffer reason=invalid value count hint=one integer")
            try:
                draft["log"]["crashlog_max_buffer"] = int(values[0])
            except ValueError as exc:
                raise SectionError("field=crashlog-max-buffer reason=invalid integer hint=use numeric value") from exc
        else:
            raise SectionError(
                "field={0} reason=unknown key hint=allowed: crashlog-report-channel,receive-mode,crashlog-max-buffer".format(key)
            )

        return self.validate_payload(draft)

    def apply_unset(self, payload: dict[str, Any], key: str) -> dict[str, Any]:
        canonical_key = "receive-mode" if key == "recive-mode" else key
        draft = deepcopy(payload)
        default = self.default_payload()

        if canonical_key == "crashlog-report-channel":
            draft["log"]["crashlog_report_channel"] = default["log"]["crashlog_report_channel"]
        elif canonical_key == "receive-mode":
            draft["log"]["receive_mode"] = default["log"]["receive_mode"]
        elif canonical_key == "crashlog-max-buffer":
            draft["log"]["crashlog_max_buffer"] = default["log"]["crashlog_max_buffer"]
        else:
            raise SectionError(
                "field={0} reason=unknown key hint=allowed: crashlog-report-channel,receive-mode,crashlog-max-buffer".format(key)
            )

        return self.validate_payload(draft)
