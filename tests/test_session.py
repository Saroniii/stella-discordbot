from utils.cli.session import SessionRegistry
from utils.cli.types import ScopeType, SessionContext


def build_session(guild_id: int, thread_id: int, actor_user_id: int) -> SessionContext:
    return SessionContext(
        session_id="s",
        guild_id=guild_id,
        thread_id=thread_id,
        actor_user_id=actor_user_id,
        scope_type=ScopeType.GUILD,
        scope_id=guild_id,
    )


def test_session_registry_acquire_release():
    registry = SessionRegistry()
    session = build_session(guild_id=1, thread_id=10, actor_user_id=100)

    assert registry.acquire(1, session) is True
    assert registry.acquire(1, session) is False
    assert registry.get(1) is session

    registry.release(1)
    assert registry.get(1) is None


def test_session_registry_get_missing_returns_none():
    registry = SessionRegistry()
    assert registry.get(999) is None


def test_session_registry_release_missing_is_noop():
    registry = SessionRegistry()
    registry.release(111)
    assert registry.get(111) is None


def test_session_registry_touch_updates_last_activity(monkeypatch):
    registry = SessionRegistry()
    session = build_session(guild_id=10, thread_id=20, actor_user_id=30)

    now = {"value": 100.0}

    def fake_time() -> float:
        return now["value"]

    monkeypatch.setattr("utils.cli.session.time.time", fake_time)
    assert registry.acquire(10, session) is True

    now["value"] = 150.0
    registry.touch(10)
    now["value"] = 200.0
    assert registry.is_expired(10, timeout_sec=49) is True
    assert registry.is_expired(10, timeout_sec=50) is False


def test_session_registry_touch_missing_is_noop():
    registry = SessionRegistry()
    registry.touch(404)
    assert registry.is_expired(404, timeout_sec=1) is True


def test_session_registry_is_expired_for_missing_session():
    registry = SessionRegistry()
    assert registry.is_expired(1, timeout_sec=300) is True


def test_session_registry_is_expired_after_timeout(monkeypatch):
    registry = SessionRegistry()
    session = build_session(guild_id=2, thread_id=10, actor_user_id=100)
    ticks = {"value": 1_000.0}

    def fake_time() -> float:
        return ticks["value"]

    monkeypatch.setattr("utils.cli.session.time.time", fake_time)
    assert registry.acquire(2, session) is True
    ticks["value"] = 1_301.0
    assert registry.is_expired(2, timeout_sec=300) is True
