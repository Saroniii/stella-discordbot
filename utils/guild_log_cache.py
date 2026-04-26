from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging


logger = logging.getLogger(__name__)


@dataclass
class CachedMessage:
    message_id: int
    channel_id: int
    author_id: int
    author_name: str
    content: str


@dataclass
class CacheCounters:
    puts: int = 0
    hits: int = 0
    misses: int = 0
    pops: int = 0
    evictions: int = 0


class GuildMessageCache:
    def __init__(self) -> None:
        self._store: dict[int, dict[int, CachedMessage]] = {}
        self._order: dict[int, deque[int]] = {}
        self._counters: dict[int, CacheCounters] = {}

    def put(self, guild_id: int, message: CachedMessage, limit: int) -> None:
        safe_limit = max(100, min(int(limit), 100000))
        guild_store = self._store.setdefault(guild_id, {})
        guild_order = self._order.setdefault(guild_id, deque())
        counters = self._counters.setdefault(guild_id, CacheCounters())
        counters.puts += 1

        if message.message_id in guild_store:
            try:
                guild_order.remove(message.message_id)
            except ValueError:
                logger.debug("guild message cache order mismatch on put: guild_id=%s message_id=%s", guild_id, message.message_id)

        guild_store[message.message_id] = message
        guild_order.append(message.message_id)
        while len(guild_order) > safe_limit:
            stale_id = guild_order.popleft()
            if stale_id in guild_store:
                guild_store.pop(stale_id, None)
                counters.evictions += 1

    def get(self, guild_id: int, message_id: int) -> CachedMessage | None:
        guild_store = self._store.get(guild_id, {})
        counters = self._counters.setdefault(guild_id, CacheCounters())
        message = guild_store.get(message_id)
        if message is None:
            counters.misses += 1
            return None
        counters.hits += 1
        return message

    def pop(self, guild_id: int, message_id: int) -> CachedMessage | None:
        guild_store = self._store.get(guild_id, {})
        counters = self._counters.setdefault(guild_id, CacheCounters())
        message = guild_store.pop(message_id, None)
        if message is None:
            counters.misses += 1
            return None
        counters.pops += 1
        guild_order = self._order.get(guild_id)
        if guild_order is not None:
            try:
                guild_order.remove(message_id)
            except ValueError:
                logger.debug("guild message cache order mismatch on pop: guild_id=%s message_id=%s", guild_id, message_id)
        return message

    def status(self, guild_id: int, configured_limit: int) -> dict[str, float | int]:
        safe_limit = max(100, min(int(configured_limit), 100000))
        guild_store = self._store.get(guild_id, {})
        counters = self._counters.setdefault(guild_id, CacheCounters())
        message_count = len(guild_store)
        content_bytes = sum(len((msg.content or "").encode("utf-8")) for msg in guild_store.values())
        estimated_bytes = sum(
            len((msg.content or "").encode("utf-8")) + len(str(msg.author_name).encode("utf-8")) + 64
            for msg in guild_store.values()
        )
        usage_percent = round((message_count / safe_limit) * 100, 1) if safe_limit > 0 else 0.0
        total_lookups = counters.hits + counters.misses
        hit_rate = round((counters.hits / total_lookups) * 100, 1) if total_lookups > 0 else 0.0
        avg_content_bytes = round(content_bytes / message_count, 1) if message_count > 0 else 0.0
        return {
            "message_count": message_count,
            "limit": safe_limit,
            "usage_percent": usage_percent,
            "content_bytes": content_bytes,
            "estimated_bytes": estimated_bytes,
            "avg_content_bytes": avg_content_bytes,
            "puts": counters.puts,
            "hits": counters.hits,
            "misses": counters.misses,
            "pops": counters.pops,
            "evictions": counters.evictions,
            "hit_rate": hit_rate,
        }

    def clear_guild(self, guild_id: int) -> None:
        self._store.pop(guild_id, None)
        self._order.pop(guild_id, None)


guild_message_cache = GuildMessageCache()
