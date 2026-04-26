from __future__ import annotations

import pytest

from fakes.discord_objects import FakeBot, FakeChannel, FakeGuild
from utils.config_runtime import extract_running_payload
from utils.discord_helpers import resolve_bot_channel, resolve_guild_channel, safe_int


def test_extract_running_payload_prefers_envelope_running_payload():
    raw = {"payload": {"running_payload": {"enabled": True}, "startup_payload": {"enabled": False}}}

    assert extract_running_payload(raw) == {"enabled": True}


def test_extract_running_payload_accepts_plain_payload():
    assert extract_running_payload({"payload": {"enabled": True}}) == {"enabled": True}
    assert extract_running_payload({"enabled": True}) == {"enabled": True}
    assert extract_running_payload(None) == {}


def test_safe_int_handles_invalid_values():
    assert safe_int("12") == 12
    assert safe_int("bad", 7) == 7
    assert safe_int(None) is None


@pytest.mark.asyncio
async def test_resolve_guild_channel_uses_cache_before_fetch():
    channel = FakeChannel(10)
    guild = FakeGuild(1, channels=[channel])

    assert await resolve_guild_channel(guild, 10) is channel
    assert guild.fetch_calls == []


@pytest.mark.asyncio
async def test_resolve_bot_channel_uses_fetch_fallback():
    channel = FakeChannel(20)
    class FetchOnlyBot(FakeBot):
        def get_channel(self, channel_id: int):
            return None

    bot = FetchOnlyBot(channels=[channel])

    assert await resolve_bot_channel(bot, 20) is channel
    assert bot.fetch_channel_calls == [20]
