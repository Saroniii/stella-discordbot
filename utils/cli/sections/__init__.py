from __future__ import annotations

from utils.cli.sections.base import SectionSpec
from utils.cli.sections.auto_reaction import AutoReactionSection
from utils.cli.sections.chat_group_global import ChatGroupGlobalSection
from utils.cli.sections.chat_group import ChatGroupConnectionSection, ChatGroupMemberGuildsSection, ChatGroupSection
from utils.cli.sections.console import ConsoleSection
from utils.cli.sections.control_plane import ControlPlaneRootConnectionSection, ControlPlaneSection, ControlPlaneTickSection
from utils.cli.sections.guild_log import GuildLogSubSection
from utils.cli.sections.level_common import LevelCommonSection
from utils.cli.sections.level_gain_policy import LevelGainPolicySection
from utils.cli.sections.level_method import LevelMethodSection
from utils.cli.sections.level_segment_table import LevelSegmentTableSection
from utils.cli.sections.level_shared import LevelSharedSection
from utils.cli.sections.level_static_table import LevelStaticTableSection
from utils.cli.sections.log_config import LogConfigSection
from utils.cli.sections.log_type import LogTypeSection
from utils.cli.sections.management_module import ManagementModuleSection
from utils.cli.sections.root_policy import (
    RootDefaultsSection,
    RootEnforceOverrideScopedSection,
    RootEnforceOverrideSection,
    RootPolicyScopedSection,
    RootEnforceSection,
)
from utils.cli.sections.sticky_message import (
    StickyChannelWebhookSection,
    StickyChannelsSection,
    StickyEmbedFieldsSection,
    StickyEmbedSection,
    StickyMessageSection,
)
from utils.cli.sections.tenant_connection import TenantConnectionLogSection
from utils.cli.sections.welcome import WelcomeSection


def build_section_registry() -> dict[str, SectionSpec]:
    base_sections = {
        "welcome": WelcomeSection(),
        "log-config": LogConfigSection(),
        "console": ConsoleSection(),
        "management-module": ManagementModuleSection(),
        "sticky-message": StickyMessageSection(),
        "sticky-message/channels": StickyChannelsSection(),
        "sticky-message/channels/webhook": StickyChannelWebhookSection(),
        "sticky-message/embed": StickyEmbedSection(),
        "sticky-message/embed/fields": StickyEmbedFieldsSection(),
        "auto-reaction": AutoReactionSection(),
        "chat-group-global": ChatGroupGlobalSection(),
        "chat-group": ChatGroupSection(),
        "chat-group/connection": ChatGroupConnectionSection(),
        "chat-group/member-guilds": ChatGroupMemberGuildsSection(),
        "level-common": LevelCommonSection(),
        "level-method-message": LevelMethodSection("message"),
        "level-method-reaction": LevelMethodSection("reaction"),
        "level-method-voice": LevelMethodSection("voice"),
        "level-shared": LevelSharedSection(),
        "level-segment-table": LevelSegmentTableSection(),
        "level-static-table": LevelStaticTableSection(),
        "level-gain-policy": LevelGainPolicySection(),
        "root-defaults": RootDefaultsSection(),
        "root-enforce": RootEnforceSection(),
        "root-enforce-override": RootEnforceOverrideSection(),
        "control-plane": ControlPlaneSection(),
        "control-plane/root-connection": ControlPlaneRootConnectionSection(),
        "control-plane/tick": ControlPlaneTickSection(),
        "tenant-connection/log": TenantConnectionLogSection(),
        "guild-log/mod-log": GuildLogSubSection("mod-log"),
        "guild-log/message-log": GuildLogSubSection("message-log"),
        "guild-log/member-log": GuildLogSubSection("member-log"),
    }

    features = {key for key in base_sections.keys() if "/" not in key}
    features.update(
        {
            "guild-log",
            "control-plane",
            "tenant-connection",
        }
    )

    base_sections["log-type"] = LogTypeSection(features)

    guild_leaf_sections = sorted(
        key
        for key in base_sections.keys()
        if key not in {"root-defaults", "root-enforce", "root-enforce-override"}
        and not key.startswith("root-defaults/")
        and not key.startswith("root-enforce/")
        and not key.startswith("root-enforce-override/")
        and not key.startswith("tenant-connection/")
        and not key.startswith("chat-group-global")
    )
    for logical in guild_leaf_sections:
        delegate = base_sections[logical]
        base_sections[f"root-defaults/{logical}"] = RootPolicyScopedSection(
            name=f"root-defaults/{logical}",
            mode="defaults",
            delegate=delegate,
        )
        base_sections[f"root-enforce/{logical}"] = RootPolicyScopedSection(
            name=f"root-enforce/{logical}",
            mode="enforce",
            delegate=delegate,
        )
        base_sections[f"root-enforce-override/{logical}"] = RootEnforceOverrideScopedSection(
            name=f"root-enforce-override/{logical}",
            delegate=delegate,
        )
    return base_sections
