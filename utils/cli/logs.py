from __future__ import annotations


def resolve_severity(levels: dict[str, str], feature: str) -> str:
    return levels.get(feature, "info")
