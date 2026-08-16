"""Bootstrap the free ephemeral database used by the Vercel function."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

import app.models  # noqa: F401 - registers every ORM table
from app.collectors.snapshot import import_snapshot
from app.core.config import settings
from app.core.logging import get_logger
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.bond import Bond

logger = get_logger(__name__)


def _snapshot_candidates() -> list[Path]:
    repository_root = Path(__file__).resolve().parents[3]
    return [
        repository_root / "data" / "snapshots" / "kase-latest.json",
        Path.cwd() / "data" / "snapshots" / "kase-latest.json",
        Path("/var/task/data/snapshots/kase-latest.json"),
    ]


def bootstrap_serverless_database() -> dict:
    """Create `/tmp` tables and seed the real, timestamped KASE snapshot."""
    if not settings.is_serverless:
        return {"status": "not_serverless"}

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        count = int(session.scalar(select(func.count(Bond.id))) or 0)
        if count:
            return {"status": "ready", "bonds": count}
        snapshot = next((path for path in _snapshot_candidates() if path.exists()), None)
        if snapshot is None:
            logger.warning("serverless snapshot was not bundled; equity live refresh remains available")
            return {"status": "empty", "bonds": 0, "snapshot": None}
        result = import_snapshot(session, snapshot, recompute=True)
        session.commit()
        logger.info("serverless database initialized from %s", snapshot)
        return {"status": "initialized", "snapshot": str(snapshot), **result}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["bootstrap_serverless_database"]
