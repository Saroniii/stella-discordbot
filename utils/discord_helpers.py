from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

_COLOR_MAP = {
    "blue": discord.Color.blue().value,
    "blurple": discord.Color.blurple().value,
    "green": discord.Color.green().value,
    "red": discord.Color.red().value,
    "orange": discord.Color.orange().value,
    "yellow": discord.Color.yellow().value,
    "purple": discord.Color.purple().value,
    "magenta": discord.Color.magenta().value,
    "teal": discord.Color.teal().value,
}


def safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def trim_text(value: str, limit: int = 300) -> str:
    text = value.replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def discord_timestamp_from_datetime(value: datetime) -> str:
    return f"<t:{int(value.timestamp())}:F>"


def discord_timestamp_now() -> str:
    return discord_timestamp_from_datetime(datetime.now(timezone.utc))


async def resolve_guild_channel(guild: Any, channel_id: int) -> Any | None:
    channel = guild.get_channel(int(channel_id)) if hasattr(guild, "get_channel") else None
    if channel is not None:
        return channel
    fetcher = getattr(guild, "fetch_channel", None)
    if fetcher is None:
        return None
    try:
        return await fetcher(int(channel_id))
    except discord.HTTPException:
        return None


async def resolve_bot_channel(bot: Any, channel_id: int) -> Any | None:
    channel = bot.get_channel(int(channel_id)) if hasattr(bot, "get_channel") else None
    if channel is not None:
        return channel
    fetcher = getattr(bot, "fetch_channel", None)
    if fetcher is None:
        return None
    try:
        return await fetcher(int(channel_id))
    except discord.HTTPException:
        return None


def parse_discord_color(raw: Any) -> discord.Color | None:
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text == "":
        return None
    if text in _COLOR_MAP:
        return discord.Color(_COLOR_MAP[text])
    if text.startswith("#"):
        text = text[1:]
    base = 16 if text.startswith("0x") else 10
    try:
        return discord.Color(int(text, base))
    except ValueError:
        return None
