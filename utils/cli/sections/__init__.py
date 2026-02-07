from __future__ import annotations

from utils.cli.sections.base import SectionSpec
from utils.cli.sections.control_plane import ControlPlaneRootConnectionSection
from utils.cli.sections.guild_log import GuildLogSubSection
from utils.cli.sections.log_config import LogConfigSection
from utils.cli.sections.log_type import LogTypeSection
from utils.cli.sections.root_policy import RootDefaultsSection, RootEnforceSection
from utils.cli.sections.tenant_connection import TenantConnectionLogSection
from utils.cli.sections.welcome import WelcomeSection


def build_section_registry() -> dict[str, SectionSpec]:
    base_sections = {
        "welcome": WelcomeSection(),
        "log-config": LogConfigSection(),
        "root-defaults": RootDefaultsSection(),
        "root-enforce": RootEnforceSection(),
        "control-plane/root-connection": ControlPlaneRootConnectionSection(),
        "tenant-connection/log": TenantConnectionLogSection(),
        "guild-log/mod-log": GuildLogSubSection("mod-log"),
        "guild-log/message-log": GuildLogSubSection("message-log"),
        "guild-log/member-log": GuildLogSubSection("member-log"),
    }

    features = set(base_sections.keys())
    features.update(
        {
            "welcome",
            "log-config",
            "guild-log",
            "mod-log",
            "message-log",
            "member-log",
            "control-plane",
            "tenant-connection",
        }
    )
    base_sections["log-type"] = LogTypeSection(features)
    return base_sections
