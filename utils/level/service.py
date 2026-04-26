from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from utils.tick import TickMeter
from utils.storage import LevelRuntimeRow, LevelUserRow, Storage


EventType = Literal["message", "reaction", "voice"]


@dataclass
class LevelEventContext:
    guild_id: int
    user_id: int
    event_type: EventType
    channel_id: int | None
    role_ids: list[int]
    occurred_at: datetime
    message_length: int = 0
    voice_seconds: int = 0


@dataclass
class LevelGainResult:
    applied_xp: int
    new_total_xp: int
    old_level: int
    new_level: int
    reason: str
    leveled_up: bool


class LevelService:
    def __init__(self, storage: Storage, tick_meter: TickMeter | None = None) -> None:
        self.storage = storage
        self.tick_meter = tick_meter or TickMeter(storage)
        self._invalid_time_warned: set[tuple[int, str, int, str, str]] = set()

    async def apply_event(self, ctx: LevelEventContext) -> LevelGainResult:
        can_start = await self.tick_meter.start_work(ctx.guild_id, f"level.event.{ctx.event_type}.entry", stoppable=True)
        if not can_start:
            return LevelGainResult(0, 0, 0, 0, "tick-over-limit", False)

        if not await self.storage.is_management_module_enabled(ctx.guild_id, "level"):
            return LevelGainResult(0, 0, 0, 0, "module-disabled", False)

        common = await self._load_running_section(ctx.guild_id, "level-common")
        method_config = await self._load_running_section(ctx.guild_id, f"level-method-{ctx.event_type}")
        shared = await self._load_running_section(ctx.guild_id, "level-shared")
        policy_config = await self._load_running_section(ctx.guild_id, "level-gain-policy")

        if ctx.event_type == "message":
            level_calc = str(common.get("level_calc", "message-count"))
            min_chars = int(common.get("min_char_count", 0))
            if level_calc == "char-count" and ctx.message_length < min_chars:
                return await self._result_without_gain(ctx, "min-char-count")

        shared_allowed = self._allow_by_shared(shared, ctx.channel_id)
        if not bool(common.get("gain_policy", False)) and not shared_allowed:
            return await self._result_without_gain(ctx, "shared-filtered")

        policy, scanned = await self._match_policy(policy_config, ctx)
        if scanned > 0:
            await self.tick_meter.consume(ctx.guild_id, "level.policy.scan", amount=scanned, stoppable=False)
        if bool(common.get("gain_policy", False)):
            if policy is None:
                return await self._result_without_gain(ctx, "policy-no-match")
            if policy.get("action") == "deny":
                return await self._result_without_gain(ctx, "policy-deny")
        elif policy is not None and policy.get("action") == "deny":
            return await self._result_without_gain(ctx, "policy-deny")

        runtime = await self.storage.get_level_runtime(ctx.guild_id, ctx.user_id)
        cooldown = self._resolve_cooldown_seconds(common, method_config, policy)
        if not self._cooldown_ready(runtime, ctx.event_type, cooldown, ctx.occurred_at):
            return await self._result_without_gain(ctx, "cooldown")

        await self.tick_meter.consume(ctx.guild_id, "level.xp.calculate", amount=1, stoppable=False)
        applied_xp = self._calculate_xp(common, method_config, policy, ctx)
        if applied_xp <= 0:
            return await self._result_without_gain(ctx, "no-gain")

        current_user = await self.storage.get_level_user(ctx.guild_id, ctx.user_id)
        old_total = current_user.total_xp if current_user else 0
        old_level = current_user.level if current_user else 0
        new_total = old_total + applied_xp
        new_level = await self._resolve_level(ctx.guild_id, common, new_total)
        await self.tick_meter.consume(ctx.guild_id, "level.xp.persist", amount=1, stoppable=False)
        await self.storage.upsert_level_user(ctx.guild_id, ctx.user_id, new_total, new_level)
        await self._touch_runtime(runtime, ctx, voice_granted=ctx.event_type == "voice")
        await self.storage.insert_level_event_log(
            guild_id=ctx.guild_id,
            user_id=ctx.user_id,
            event_type=ctx.event_type,
            applied_xp=applied_xp,
            total_xp=new_total,
            level=new_level,
            reason="applied",
            detail_json={
                "channel_id": ctx.channel_id,
                "voice_seconds": ctx.voice_seconds,
                "policy_action": policy.get("action") if policy else None,
            },
        )
        return LevelGainResult(applied_xp, new_total, old_level, new_level, "applied", new_level > old_level)

    async def mark_voice_join(self, guild_id: int, user_id: int, occurred_at: datetime) -> None:
        can_start = await self.tick_meter.start_work(guild_id, "level.event.voice.entry", stoppable=True)
        if not can_start:
            return
        await self.storage.upsert_level_runtime(guild_id, user_id, voice_joined_at=occurred_at.astimezone(timezone.utc).isoformat())

    async def apply_voice_leave(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int | None,
        role_ids: list[int],
        occurred_at: datetime,
    ) -> LevelGainResult:
        runtime = await self.storage.get_level_runtime(guild_id, user_id)
        if runtime.voice_joined_at is None:
            return LevelGainResult(0, 0, 0, 0, "voice-no-session", False)
        joined_at = _parse_iso(runtime.voice_joined_at)
        if joined_at is None:
            await self.storage.upsert_level_runtime(guild_id, user_id, clear_voice_joined_at=True)
            return LevelGainResult(0, 0, 0, 0, "voice-invalid-session", False)

        end_at = occurred_at.astimezone(timezone.utc)
        if end_at <= joined_at:
            await self.storage.upsert_level_runtime(guild_id, user_id, clear_voice_joined_at=True)
            return LevelGainResult(0, 0, 0, 0, "voice-no-duration", False)

        capped_joined = max(joined_at, end_at - timedelta(days=1))
        duration = int((end_at - capped_joined).total_seconds())
        ctx = LevelEventContext(
            guild_id=guild_id,
            user_id=user_id,
            event_type="voice",
            channel_id=channel_id,
            role_ids=role_ids,
            occurred_at=end_at,
            voice_seconds=duration,
        )
        result = await self.apply_event(ctx)
        await self.storage.upsert_level_runtime(guild_id, user_id, clear_voice_joined_at=True)
        return result

    async def get_rank_snapshot(self, guild_id: int, user_id: int) -> dict[str, Any]:
        row = await self.storage.get_level_user(guild_id, user_id)
        total_xp = row.total_xp if row else 0
        level = row.level if row else 0
        ranking = await self.storage.fetch_level_ranking(guild_id, 1000)
        rank = next((index + 1 for index, item in enumerate(ranking) if item.user_id == user_id), None)
        next_threshold = await self._next_level_threshold(guild_id, total_xp, level)
        return {
            "user_id": user_id,
            "total_xp": total_xp,
            "level": level,
            "rank": rank,
            "next_level_xp": next_threshold,
        }

    async def _result_without_gain(self, ctx: LevelEventContext, reason: str) -> LevelGainResult:
        current = await self.storage.get_level_user(ctx.guild_id, ctx.user_id)
        return LevelGainResult(
            applied_xp=0,
            new_total_xp=current.total_xp if current else 0,
            old_level=current.level if current else 0,
            new_level=current.level if current else 0,
            reason=reason,
            leveled_up=False,
        )

    async def _touch_runtime(self, runtime: LevelRuntimeRow, ctx: LevelEventContext, voice_granted: bool) -> None:
        timestamp = ctx.occurred_at.astimezone(timezone.utc).isoformat()
        kwargs: dict[str, Any] = {}
        if ctx.event_type == "message":
            kwargs["last_message_at"] = timestamp
        elif ctx.event_type == "reaction":
            kwargs["last_reaction_at"] = timestamp
        elif ctx.event_type == "voice" and voice_granted:
            kwargs["last_voice_grant_at"] = timestamp
        if kwargs:
            await self.storage.upsert_level_runtime(ctx.guild_id, ctx.user_id, **kwargs)

    async def _load_running_section(self, guild_id: int, section: str) -> dict[str, Any]:
        stored = await self.storage.load_config("guild", guild_id, section)
        if stored is None:
            return {}
        raw = stored.data if isinstance(stored.data, dict) else {}
        payload = raw.get("payload", raw)
        if not isinstance(payload, dict):
            return {}
        running = payload.get("running_payload")
        if isinstance(running, dict):
            return dict(running)
        return dict(payload)

    def _allow_by_shared(self, shared: dict[str, Any], channel_id: int | None) -> bool:
        mode = str(shared.get("mode", "blacklist"))
        channels = shared.get("channels", [])
        if not isinstance(channels, list):
            channels = []
        if channel_id is None:
            return True
        if mode == "whitelist":
            return channel_id in channels
        return channel_id not in channels

    async def _match_policy(self, policy_config: dict[str, Any], ctx: LevelEventContext) -> tuple[dict[str, Any] | None, int]:
        rules = policy_config.get("policies", []) if isinstance(policy_config, dict) else []
        if not isinstance(rules, list):
            return None, 0
        sorted_rules = sorted(
            (rule for rule in rules if isinstance(rule, dict)),
            key=lambda item: (_safe_int(item.get("id"), 0) == 0, _safe_int(item.get("id"), 0)),
        )
        scanned = 0
        for rule in sorted_rules:
            scanned += 1
            if not await self._rule_matches(rule, ctx):
                continue
            return rule, scanned
        return None, scanned

    async def _rule_matches(self, rule: dict[str, Any], ctx: LevelEventContext) -> bool:
        method = str(rule.get("method", "any"))
        if method not in {"any", ctx.event_type}:
            return False

        channels = rule.get("channels", "any")
        if channels != "any":
            if ctx.channel_id is None:
                return False
            if not isinstance(channels, list) or ctx.channel_id not in channels:
                return False

        roles = rule.get("roles", "any")
        if roles != "any":
            if not isinstance(roles, list):
                return False
            parsed_roles = {_safe_int(item) for item in roles}
            parsed_roles.discard(None)
            if not parsed_roles:
                return False
            if not set(ctx.role_ids) & parsed_roles:
                return False

        start = rule.get("time_start")
        end = rule.get("time_end")
        if start and end:
            start_t = _parse_hhmm(start)
            end_t = _parse_hhmm(end)
            if start_t is None or end_t is None:
                await self._warn_invalid_policy_time(rule, ctx, str(start), str(end))
                return False
            if not _time_window_match(start_t, end_t, ctx.occurred_at):
                return False
        return True

    async def _warn_invalid_policy_time(
        self,
        rule: dict[str, Any],
        ctx: LevelEventContext,
        start: str,
        end: str,
    ) -> None:
        minute_key = ctx.occurred_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        rule_id = _safe_int(rule.get("id"), 0) or 0
        key = (ctx.guild_id, minute_key, rule_id, start, end)
        if key in self._invalid_time_warned:
            return
        self._invalid_time_warned.add(key)
        logged = await self.storage.insert_system_log_safe(
            actor_user_id=None,
            scope_id=ctx.guild_id,
            feature="level-policy",
            severity="warn",
            message="invalid-policy-time-window",
            detail_json={
                "guild_id": ctx.guild_id,
                "rule_id": rule_id,
                "time_start": start,
                "time_end": end,
                "event_type": ctx.event_type,
            },
        )
        if logged:
            await self.tick_meter.consume(ctx.guild_id, "log.system.write", amount=1, stoppable=False)

    def _resolve_cooldown_seconds(self, common: dict[str, Any], method_cfg: dict[str, Any], policy: dict[str, Any] | None) -> int:
        if policy and policy.get("action") == "override":
            return int(policy.get("gain_time", method_cfg.get("gain_time", common.get("gain_time", 10))))
        return int(method_cfg.get("gain_time", common.get("gain_time", 10)))

    def _cooldown_ready(self, runtime: LevelRuntimeRow, event_type: EventType, cooldown_sec: int, now: datetime) -> bool:
        now_utc = now.astimezone(timezone.utc)
        if cooldown_sec <= 0:
            return True
        field_value: str | None
        if event_type == "message":
            field_value = runtime.last_message_at
        elif event_type == "reaction":
            field_value = runtime.last_reaction_at
        else:
            field_value = runtime.last_voice_grant_at
        if not field_value:
            return True
        prev = _parse_iso(field_value)
        if prev is None:
            return True
        return (now_utc - prev).total_seconds() >= cooldown_sec

    def _calculate_xp(self, common: dict[str, Any], method_cfg: dict[str, Any], policy: dict[str, Any] | None, ctx: LevelEventContext) -> int:
        source = method_cfg
        if policy and policy.get("action") == "override":
            source = {**method_cfg, **policy}

        gain_mode = str(source.get("gain_mode", "static"))
        if gain_mode == "random-range":
            min_value = int(source.get("gain_range_min", 1))
            max_value = int(source.get("gain_range_max", min_value))
            if max_value < min_value:
                max_value = min_value
            base = random.randint(min_value, max_value)
        else:
            base = int(source.get("gain_xp", 1))

        if ctx.event_type == "voice":
            period = int(source.get("gain_time", common.get("gain_time", 10)))
            if period <= 0:
                period = 1
            chunks = max(0, int(ctx.voice_seconds // period))
            if chunks == 0:
                return 0
            if gain_mode == "random-range":
                min_value = int(source.get("gain_range_min", 1))
                max_value = int(source.get("gain_range_max", min_value))
                return int(sum(random.randint(min_value, max_value) for _ in range(chunks)) * float(common.get("multiplier", 1.0)))
            return int(base * chunks * float(common.get("multiplier", 1.0)))

        return int(base * float(common.get("multiplier", 1.0)))

    async def _resolve_level(self, guild_id: int, common: dict[str, Any], total_xp: int) -> int:
        rows = await self.storage.fetch_level_table(guild_id, limit=10000)
        if rows:
            level = 0
            for row in rows:
                if total_xp >= int(row["required_total_xp"]):
                    level = int(row["level"])
                else:
                    break
            return level

        fixed_step = int(common.get("fixed_step", 100))
        max_level = int(common.get("max_level", 100))
        if fixed_step <= 0:
            return 0
        return min(max_level, total_xp // fixed_step)

    async def _next_level_threshold(self, guild_id: int, total_xp: int, level: int) -> int | None:
        rows = await self.storage.fetch_level_table(guild_id, limit=10000)
        if rows:
            for row in rows:
                threshold = int(row["required_total_xp"])
                if threshold > total_xp:
                    return threshold
            return None
        return (level + 1) * 100


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time_window_match(start_t, end_t, occurred_at: datetime) -> bool:
    current = occurred_at.astimezone(timezone.utc).time()
    if start_t <= end_t:
        return start_t <= current <= end_t
    return current >= start_t or current <= end_t


def _parse_hhmm(value: str):
    try:
        hour, minute = value.split(":")
        hour_v = int(hour)
        minute_v = int(minute)
    except Exception:
        return None
    if hour_v < 0 or hour_v > 23 or minute_v < 0 or minute_v > 59:
        return None
    return datetime(2000, 1, 1, hour_v, minute_v, tzinfo=timezone.utc).time()


def _safe_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
