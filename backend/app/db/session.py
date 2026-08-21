"""Engine / session factory."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

_connect_args: dict = {}
if _is_sqlite:
    # SQLite is the development and test database; production runs PostgreSQL.
    # The scheduler writes from background tasks while requests write from the
    # event loop, so a plain SQLite connection raises "database is locked" the
    # moment a save lands during a refresh. `timeout` makes a writer wait for
    # the lock instead of failing immediately.
    _connect_args = {"check_same_thread": False, "timeout": 30.0}

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:  # pragma: no cover - driver level
        """Let readers and one writer coexist.

        In the default rollback-journal mode any write blocks every reader, so
        a ten-minute monitoring pass can fail a user's request that merely
        reads. WAL lets them overlap; `busy_timeout` covers the writer-versus-
        writer case that WAL alone does not. Both are no-ops on PostgreSQL,
        which is why they are attached only to the SQLite engine.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a transactional session."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
