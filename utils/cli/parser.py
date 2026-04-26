from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass
class ParsedCommand:
    name: str
    args: list[str]


@dataclass
class ParseError:
    reason: str


def parse_line(line: str) -> ParsedCommand | ParseError | None:
    stripped = line.strip().replace("？", "?")
    if not stripped:
        return None
    if stripped.startswith("#"):
        return None

    try:
        tokens = shlex.split(stripped)
    except ValueError as exc:
        return ParseError(reason=str(exc))

    if not tokens:
        return None

    return ParsedCommand(name=tokens[0].lower(), args=tokens[1:])
