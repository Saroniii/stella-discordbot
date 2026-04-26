from __future__ import annotations

import logging

from utils.guild_log_cache import CachedMessage, GuildMessageCache


def test_guild_log_cache_logs_order_mismatch_on_put(caplog):
    cache = GuildMessageCache()
    guild_id = 1
    message = CachedMessage(message_id=10, channel_id=100, author_id=200, author_name="a", content="x")
    cache.put(guild_id, message, limit=100)
    cache._order[guild_id].clear()

    with caplog.at_level(logging.DEBUG):
        cache.put(guild_id, message, limit=100)

    assert "order mismatch on put" in caplog.text


def test_guild_log_cache_logs_order_mismatch_on_pop(caplog):
    cache = GuildMessageCache()
    guild_id = 2
    message = CachedMessage(message_id=20, channel_id=100, author_id=200, author_name="a", content="x")
    cache.put(guild_id, message, limit=100)
    cache._order[guild_id].clear()

    with caplog.at_level(logging.DEBUG):
        popped = cache.pop(guild_id, message.message_id)

    assert popped is not None
    assert "order mismatch on pop" in caplog.text
