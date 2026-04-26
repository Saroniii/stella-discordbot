from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from utils.cli.sections.base import FieldRule, MappedSectionSpec, PydanticMappedSectionSpec
from utils.cli.types import ManagementModuleConfigV1


class ManagementModuleSection(PydanticMappedSectionSpec):
    name = "management-module"
    schema_version = 1
    model_type: ClassVar[type[BaseModel]] = ManagementModuleConfigV1
    field_rules = {
        "welcome": FieldRule(
            path=("welcome",),
            parser=MappedSectionSpec.parse_enable_disable("welcome"),
            candidates=["enable", "disable"],
        ),
        "level": FieldRule(
            path=("level",),
            parser=MappedSectionSpec.parse_enable_disable("level"),
            candidates=["enable", "disable"],
        ),
        "sticky-message": FieldRule(
            path=("sticky_message",),
            parser=MappedSectionSpec.parse_enable_disable("sticky-message"),
            candidates=["enable", "disable"],
        ),
        "auto-reaction": FieldRule(
            path=("auto_reaction",),
            parser=MappedSectionSpec.parse_enable_disable("auto-reaction"),
            candidates=["enable", "disable"],
        ),
    }
