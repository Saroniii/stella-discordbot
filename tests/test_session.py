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
