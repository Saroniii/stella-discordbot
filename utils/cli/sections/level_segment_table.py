from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from utils.cli.sections.level_table import LevelXpTableSection
from utils.cli.types import LevelSegmentTableConfigV1


class LevelSegmentTableSection(LevelXpTableSection):
    name = "level-segment-table"
    schema_version = 1
    model_type: ClassVar[type[BaseModel]] = LevelSegmentTableConfigV1
    xp_candidate = "<xp>"
