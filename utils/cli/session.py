from __future__ import annotations

import time
from dataclasses import dataclass

from utils.cli.types import SessionContext


@dataclass
class SessionEntry:
    session: SessionContext
    last_activity: float


class SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[int, SessionEntry] = {}

    def acquire(self, guild_id: int, session: SessionContext) -> bool:
        if guild_id in self._sessions:
            return False
        self._sessions[guild_id] = SessionEntry(session=session, last_activity=time.time())
        return True

    def get(self, guild_id: int) -> SessionContext | None:
        entry = self._sessions.get(guild_id)
        return entry.session if entry else None

    def touch(self, guild_id: int) -> None:
        if guild_id in self._sessions:
            self._sessions[guild_id].last_activity = time.time()

    def is_expired(self, guild_id: int, timeout_sec: int) -> bool:
        entry = self._sessions.get(guild_id)
        if not entry:
            return True
        return (time.time() - entry.last_activity) > timeout_sec

    def release(self, guild_id: int) -> None:
        self._sessions.pop(guild_id, None)
