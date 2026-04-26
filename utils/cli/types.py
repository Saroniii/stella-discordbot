from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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
    tracking_message_mode: Literal["normal", "extra"] = "normal"
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


class RootEnforceOverrideEntryV1(StrictModel):
    sections: dict[str, dict[str, Any]] = Field(default_factory=dict)


class RootEnforceOverrideConfigV1(StrictModel):
    guilds: dict[str, RootEnforceOverrideEntryV1] = Field(default_factory=dict)


class ControlPlaneRootConnectionV1(StrictModel):
    send_crashlog_root: bool = False


class ControlPlaneTickV1(StrictModel):
    max_tick_limit: int | None = Field(default=None, ge=100, le=1000000)
    overlimit_mode: Literal["alert-only", "drop-new-work"] | None = None


class ControlPlaneConfigV1(StrictModel):
    timezone: str | None = None
    root_connection: ControlPlaneRootConnectionV1 = Field(default_factory=ControlPlaneRootConnectionV1)
    tick: ControlPlaneTickV1 = Field(default_factory=ControlPlaneTickV1)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("invalid timezone") from exc
        return value


class ChatGroupGlobalConfigV1(StrictModel):
    attachment_channel_id: int | None = None


class ChatGroupConnectionConfigV1(StrictModel):
    channel: int | None = None
    webhook: str | None = None
    name_format: str = "{nickname} / {guild_name}"


class ChatGroupMemberGuildConfigV1(StrictModel):
    id: int = Field(ge=1)
    guild: int = 0
    status: Literal["active", "pending", "disable"] = "active"
    role: Literal["leader", "manager", "normal"] = "normal"


class ChatGroupItemConfigV1(StrictModel):
    id: int = Field(ge=1)
    name: str = ""
    group_id: str = ""
    mode: Literal["discovery", "public", "private"] = "public"
    join_need_apply: bool = False
    status: Literal["active", "disable"] = "active"
    connection: ChatGroupConnectionConfigV1 = Field(default_factory=ChatGroupConnectionConfigV1)
    member_guilds: list[ChatGroupMemberGuildConfigV1] = Field(default_factory=list)


class ChatGroupConfigV1(StrictModel):
    groups: list[ChatGroupItemConfigV1] = Field(default_factory=list)


class TenantConnectionLogV1(StrictModel):
    crashlog_report_channel: int | None = None
    receive_mode: Literal["off", "discord", "database", "both"] = "off"
    crashlog_max_buffer: int = Field(default=500, ge=100, le=100000)


class TenantConnectionTickV1(StrictModel):
    max_tick_limit: int = Field(default=3000, ge=100, le=1000000)
    overlimit_mode: Literal["alert-only", "drop-new-work"] = "alert-only"


class TenantConnectionConfigV1(StrictModel):
    log: TenantConnectionLogV1 = Field(default_factory=TenantConnectionLogV1)
    tick: TenantConnectionTickV1 = Field(default_factory=TenantConnectionTickV1)


class ConsoleConfigV1(StrictModel):
    always_print_help: bool = False
    console_mode: Literal["thread", "channel"] = "thread"
    thread_console_after_delete: bool = False


class ManagementModuleConfigV1(StrictModel):
    welcome: bool = True
    level: bool = False
    sticky_message: bool = False
    auto_reaction: bool = False


class StickyWebhookConfigV1(StrictModel):
    name: str = ""
    icon: str | None = None
    webhook: str | None = None


class StickyChannelConfigV1(StrictModel):
    id: int = Field(ge=1)
    channel_id: int | None = None
    send_mode: Literal["bot", "webhook"] = "bot"
    webhook: StickyWebhookConfigV1 = Field(default_factory=StickyWebhookConfigV1)


class StickyEmbedFieldConfigV1(StrictModel):
    id: int = Field(ge=1)
    name: str = ""
    value: str = ""
    inline_mode: bool = False


class StickyEmbedConfigV1(StrictModel):
    title: str = ""
    description: str = ""
    color: str | None = None
    avatar_url: str | None = None
    footer: str | None = None
    fields: list[StickyEmbedFieldConfigV1] = Field(default_factory=list)


class StickyMessageItemConfigV2(StrictModel):
    id: int = Field(ge=1)
    message: str = ""
    delay: int = Field(default=0, ge=0, le=3600)
    trigger_bot_message: bool = False
    channels: list[StickyChannelConfigV1] = Field(default_factory=list)
    embed: StickyEmbedConfigV1 = Field(default_factory=StickyEmbedConfigV1)


class StickyMessageConfigV1(StrictModel):
    items: list[StickyMessageItemConfigV2] = Field(default_factory=list)


class AutoReactionRuleV1(StrictModel):
    id: int = Field(ge=1)
    channels: list[int] = Field(default_factory=list)
    emojis: list[str] = Field(default_factory=list)


class AutoReactionConfigV1(StrictModel):
    rules: list[AutoReactionRuleV1] = Field(default_factory=list)


class LevelCommonConfigV1(StrictModel):
    level_calc: Literal["message-count", "char-count"] = "message-count"
    level_table: Literal["fixed", "function", "segment-interpolation", "static-table"] = "fixed"
    gain_policy: bool = False
    max_level: int = Field(default=100, ge=1, le=1000)
    gain_time: int = Field(default=10, ge=1, le=86400)
    multiplier: float = Field(default=1.0, ge=0)
    min_char_count: int = Field(default=0, ge=0, le=10000)
    fixed_step: int = Field(default=100, ge=1, le=1000000)
    function_type: Literal["exponential", "quadratic"] = "exponential"
    function_base: int = Field(default=100, ge=1, le=1000000)
    function_rate: float = Field(default=1.2, gt=1.0, le=10.0)
    function_a: float = 1.0
    function_b: float = 10.0
    function_c: float = 0.0
    levelup_channel: int | None = None
    levelup_message: str | None = None


class LevelMethodConfigV1(StrictModel):
    gain_mode: Literal["static", "random-range"] = "static"
    gain_xp: int = Field(default=1, ge=0, le=100000)
    gain_range_min: int = Field(default=1, ge=0, le=100000)
    gain_range_max: int = Field(default=1, ge=0, le=100000)
    gain_time: int = Field(default=10, ge=1, le=86400)


class LevelSharedConfigV1(StrictModel):
    mode: Literal["blacklist", "whitelist"] = "blacklist"
    channels: list[int] = Field(default_factory=list)


class LevelSegmentTableConfigV1(StrictModel):
    entries: dict[str, int] = Field(default_factory=dict)


class LevelStaticTableConfigV1(StrictModel):
    entries: dict[str, int] = Field(default_factory=dict)


class LevelGainPolicyRuleV1(StrictModel):
    id: int = Field(ge=0)
    name: str = ""
    action: Literal["deny", "gain", "override"] = "gain"
    channels: list[int] | Literal["any"] = "any"
    roles: list[int] | Literal["any"] = "any"
    method: Literal["message", "voice", "reaction", "any"] = "any"
    gain_mode: Literal["static", "random-range"] = "static"
    gain_xp: int = Field(default=1, ge=0, le=100000)
    gain_range_min: int = Field(default=1, ge=0, le=100000)
    gain_range_max: int = Field(default=1, ge=0, le=100000)
    gain_time: int = Field(default=10, ge=1, le=86400)
    time_start: str | None = None
    time_end: str | None = None


class LevelGainPolicyConfigV1(StrictModel):
    policies: list[LevelGainPolicyRuleV1] = Field(default_factory=list)


@dataclass
class EngineContext:
    actor_user_id: int
    guild_id: int
    channel_id: int
    is_bot_admin: bool
    has_manage_guild: bool
    guild: Any | None = None


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
    selected_map: dict[str, str] = field(default_factory=dict)
    running_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    startup_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    cli_log_stream_enabled: bool = False
    cli_log_no_message_response: bool = False
    cli_log_started_at: str | None = None


@dataclass
class EngineResult:
    output: str
    prompt: str
    should_exit: bool = False
