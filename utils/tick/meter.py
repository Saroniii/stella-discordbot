from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

from utils.storage import Storage, TickConfig


@dataclass
class TickBucket:
    minute_key: str
    limit: int = 3000
    used: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    warned90: bool = False
    warned_over: bool = False


@dataclass
class TickHistoryRow:
    minute_key: str
    used: int
    limit: int
    usage_percent: float


class TickMeter:
    def __init__(self, storage: Storage, history_minutes: int = 30) -> None:
        self.storage = storage
        self.history_minutes = history_minutes
        self._buckets: dict[int, TickBucket] = {}
        self._history: dict[int, deque[TickHistoryRow]] = {}

    async def start_work(self, guild_id: int, source: str, stoppable: bool) -> bool:
        config = await self.storage.resolve_tick_config(guild_id)
        bucket = self._ensure_bucket(guild_id)
        bucket.limit = config.max_tick_limit
        if config.overlimit_mode == "drop-new-work" and stoppable and bucket.used >= config.max_tick_limit:
            await self._emit_alerts(guild_id, bucket, config)
            return False
        await self.consume(guild_id, source, amount=1, stoppable=False)
        return True

    async def consume(self, guild_id: int, source: str, amount: int = 1, stoppable: bool = False) -> bool:
        config = await self.storage.resolve_tick_config(guild_id)
        bucket = self._ensure_bucket(guild_id)
        bucket.limit = config.max_tick_limit
        if config.overlimit_mode == "drop-new-work" and stoppable and bucket.used >= config.max_tick_limit:
            await self._emit_alerts(guild_id, bucket, config)
            return False

        bucket.used += max(0, int(amount))
        bucket.by_source[source] = bucket.by_source.get(source, 0) + max(0, int(amount))
        await self._emit_alerts(guild_id, bucket, config)
        return True

    async def get_status(self, guild_id: int) -> dict:
        config = await self.storage.resolve_tick_config(guild_id)
        bucket = self._ensure_bucket(guild_id)
        used = bucket.used
        limit = max(1, config.max_tick_limit)
        usage_percent = round((used / limit) * 100, 1)

        category_totals: dict[str, int] = {}
        for source, value in bucket.by_source.items():
            category = self._category_of(source)
            category_totals[category] = category_totals.get(category, 0) + value

        category_rows = []
        for category, value in sorted(category_totals.items(), key=lambda item: item[1], reverse=True):
            share = round((value / used) * 100, 1) if used > 0 else 0.0
            category_rows.append({"category": category, "used": value, "share_percent": share})

        source_rows = []
        for source, value in sorted(bucket.by_source.items(), key=lambda item: item[1], reverse=True):
            share = round((value / used) * 100, 1) if used > 0 else 0.0
            source_rows.append({"source": source, "used": value, "share_percent": share})

        history_rows = list(self._history.get(guild_id, deque()))
        return {
            "minute": bucket.minute_key,
            "used": used,
            "limit": limit,
            "usage_percent": usage_percent,
            "mode": config.overlimit_mode,
            "categories": category_rows,
            "sources": source_rows,
            "history": [
                {
                    "minute": row.minute_key,
                    "used": row.used,
                    "limit": row.limit,
                    "usage_percent": row.usage_percent,
                }
                for row in history_rows
            ],
        }

    def _ensure_bucket(self, guild_id: int) -> TickBucket:
        minute = _minute_key(datetime.now(timezone.utc))
        current = self._buckets.get(guild_id)
        if current is None:
            current = TickBucket(minute_key=minute)
            self._buckets[guild_id] = current
            return current
        if current.minute_key != minute:
            self._archive_bucket(guild_id, current)
            current = TickBucket(minute_key=minute)
            self._buckets[guild_id] = current
        return current

    def _archive_bucket(self, guild_id: int, bucket: TickBucket) -> None:
        history = self._history.setdefault(guild_id, deque(maxlen=self.history_minutes))
        limit = max(1, bucket.limit)
        usage_percent = round((bucket.used / limit) * 100, 1) if bucket.used > 0 else 0.0
        history.appendleft(
            TickHistoryRow(
                minute_key=bucket.minute_key,
                used=bucket.used,
                limit=limit,
                usage_percent=usage_percent,
            )
        )

    async def _emit_alerts(self, guild_id: int, bucket: TickBucket, config: TickConfig) -> None:
        limit = max(1, config.max_tick_limit)
        usage = (bucket.used / limit) * 100
        if usage >= 90 and not bucket.warned90:
            bucket.warned90 = True
            logged = await self.storage.insert_system_log_safe(
                actor_user_id=None,
                scope_id=guild_id,
                feature="tick",
                severity="warn",
                message="tick-usage-90",
                detail_json={
                    "guild_id": guild_id,
                    "used": bucket.used,
                    "limit": limit,
                    "usage_percent": round(usage, 1),
                    "mode": config.overlimit_mode,
                },
            )
            if logged:
                bucket.by_source["log.system.write"] = bucket.by_source.get("log.system.write", 0) + 1
                bucket.used += 1

        if bucket.used > limit and not bucket.warned_over:
            bucket.warned_over = True
            logged = await self.storage.insert_system_log_safe(
                actor_user_id=None,
                scope_id=guild_id,
                feature="tick",
                severity="warn",
                message="tick-over-limit",
                detail_json={
                    "guild_id": guild_id,
                    "used": bucket.used,
                    "limit": limit,
                    "usage_percent": round((bucket.used / limit) * 100, 1),
                    "mode": config.overlimit_mode,
                },
            )
            if logged:
                bucket.by_source["log.system.write"] = bucket.by_source.get("log.system.write", 0) + 1
                bucket.used += 1

    def _category_of(self, source: str) -> str:
        if source.startswith("level."):
            return "level"
        if source.startswith("log."):
            return "log"
        if source.startswith("command.") or source.startswith("cli."):
            return "command"
        if source.startswith("storage."):
            return "storage"
        return "other"


def _minute_key(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
