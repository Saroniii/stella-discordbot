from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from utils.cli.sections.level_table import LevelXpTableSection
from utils.cli.types import LevelStaticTableConfigV1


class LevelStaticTableSection(LevelXpTableSection):
    name = "level-static-table"
    schema_version = 1
    model_type: ClassVar[type[BaseModel]] = LevelStaticTableConfigV1
    xp_candidate = "<delta-xp>"
