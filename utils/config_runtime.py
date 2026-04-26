from __future__ import annotations

from typing import Any

from utils.storage import Storage


def extract_running_payload(raw: dict[str, Any] | None) -> dict[str, Any]:
    payload = raw.get("payload", raw) if isinstance(raw, dict) else {}
    if isinstance(payload, dict) and isinstance(payload.get("running_payload"), dict):
        return dict(payload["running_payload"])
    if isinstance(payload, dict):
        return dict(payload)
    return {}


async def load_running_section(storage: Storage, scope_type: str, scope_id: int, section: str) -> dict[str, Any]:
    stored = await storage.load_config(scope_type, scope_id, section)
    if stored is None or not isinstance(stored.data, dict):
        return {}
    return extract_running_payload(stored.data)


async def load_guild_running_section(storage: Storage, guild_id: int, section: str) -> dict[str, Any]:
    return await load_running_section(storage, "guild", guild_id, section)


async def ensure_bind_ready(bot: Any) -> None:
    if hasattr(bot, "ensure_config_bound"):
        await bot.ensure_config_bound()
    bind_event = getattr(bot, "config_bind_ready", None)
    if bind_event is not None:
        await bind_event.wait()
