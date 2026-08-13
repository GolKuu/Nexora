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


async def refresh_catalog_via_browser(limit: int | None = None) -> dict:
    """Sweep the public catalogue with the browser agent (§28).

    This is the periodic path: the browser refreshes the database, and user
    requests read the database. Browsing on every page view would be both slow
    and rude to the exchange.
    """
    from app.services.browser_agent_service import BrowserAgentService

    session = SessionLocal()
    try:
        result = await BrowserAgentService(session).refresh_catalog(limit=limit)
        logger.info("browser catalogue sweep: %s", result)
        return result
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
