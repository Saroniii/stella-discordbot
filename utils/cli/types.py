from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScopeType(StrEnum):
    ROOT = "root"
    GUILD = "guild"


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ConfigEnvelope(StrictModel):
    schema_version: int
    payload: dict[str, Any]


class WelcomeConfigV1(StrictModel):
    join_roles: list[int] = Field(default_factory=list)
    welcome_message: str | None = None


class LogConfigV1(StrictModel):
    audit_log_max_buffer: int = Field(default=10000, ge=100, le=100000)
    system_log_max_buffer: int = Field(default=10000, ge=100, le=100000)


class LogTypeConfigV1(StrictModel):
    levels: dict[str, Literal["debug", "info", "warn", "error"]] = Field(default_factory=dict)


class GuildModLogConfigV1(StrictModel):
    channel: int | None = None
    types: list[Literal["ban", "unban", "kick", "warn", "timeout", "mute", "unmute"]] = Field(default_factory=list)


class GuildMessageLogConfigV1(StrictModel):
    channel: int | None = None
    tracking_message_count: int = Field(default=1000, ge=100, le=100000)
    categories: list[Literal["delete", "edit"]] = Field(default_factory=list)


class GuildMemberLogConfigV1(StrictModel):
    channel: int | None = None
    categories: list[Literal["join", "leave", "nickname", "role", "avatar"]] = Field(default_factory=list)


class GuildLogConfigV1(StrictModel):
    mod_log: GuildModLogConfigV1 = Field(default_factory=GuildModLogConfigV1)
    message_log: GuildMessageLogConfigV1 = Field(default_factory=GuildMessageLogConfigV1)
    member_log: GuildMemberLogConfigV1 = Field(default_factory=GuildMemberLogConfigV1)


class RootDefaultsConfigV1(StrictModel):
    sections: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RootEnforceConfigV1(StrictModel):
    # section -> key -> value
    sections: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ControlPlaneRootConnectionV1(StrictModel):
    send_crashlog_root: bool = False


class ControlPlaneConfigV1(StrictModel):
    root_connection: ControlPlaneRootConnectionV1 = Field(default_factory=ControlPlaneRootConnectionV1)


class TenantConnectionLogV1(StrictModel):
    crashlog_report_channel: int | None = None
    receive_mode: Literal["off", "discord", "database", "both"] = "off"
    crashlog_max_buffer: int = Field(default=500, ge=100, le=100000)


class TenantConnectionConfigV1(StrictModel):
    log: TenantConnectionLogV1 = Field(default_factory=TenantConnectionLogV1)


@dataclass
class EngineContext:
    actor_user_id: int
    guild_id: int
    channel_id: int
    is_bot_admin: bool
    has_manage_guild: bool


@dataclass
class SessionContext:
    session_id: str
    guild_id: int
    thread_id: int
    actor_user_id: int
    scope_type: ScopeType
    scope_id: int
    current_path: list[str] = field(default_factory=list)
    selected_object: str | None = None
    candidate_map: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class EngineResult:
    output: str
    prompt: str
    should_exit: bool = False
