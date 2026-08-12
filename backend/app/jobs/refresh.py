"""Refresh jobs, callable from the scheduler, the CLI or an admin endpoint."""

from __future__ import annotations

from app.collectors.kase_collector import KaseCollector, full_sync
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.factory import get_provider

logger = get_logger(__name__)


async def refresh_all() -> dict:
    """Reference data, quotes, trades, metrics and scores."""
    session = SessionLocal()
    try:
        summary = await full_sync(session, get_provider())
        logger.info("full refresh finished: %s", summary)
        return summary
    finally:
        session.close()


async def refresh_quotes() -> dict:
    """Quotes plus the derived metrics and scores that depend on them."""
    session = SessionLocal()
    try:
        collector = KaseCollector(session, get_provider())
        result = await collector.sync_quotes()
        result.update(collector.recompute_all())
        return result
    finally:
        session.close()
