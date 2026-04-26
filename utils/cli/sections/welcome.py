from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from utils.cli.sections.base import FieldRule, MappedSectionSpec, PydanticMappedSectionSpec
from utils.cli.types import WelcomeConfigV1


class WelcomeSection(PydanticMappedSectionSpec):
    name = "welcome"
    schema_version = 1
    model_type: ClassVar[type[BaseModel]] = WelcomeConfigV1
    field_rules = {
        "join-roles": FieldRule(
            path=("join_roles",),
            parser=MappedSectionSpec.parse_nonempty_int_list(
                "join-roles",
                empty_hint="provide one or more role ids",
                invalid_hint="use numeric role ids",
            ),
            candidates=["<role-id...>"],
        ),
        "welcome-message": FieldRule(
            path=("welcome_message",),
            parser=MappedSectionSpec.parse_single_string("welcome-message", hint="use quoted string"),
            candidates=['"<text>"'],
        ),
    }
