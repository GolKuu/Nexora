"""Fresh public KASE market snapshots for the equity ranking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collectors.kase_stock_catalog import KaseStockCatalogCollector
from app.core.config import settings
from app.models.stock import Stock

_refresh_lock = asyncio.Lock()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


async def ensure_fresh_stock_market(session: Session) -> dict:
    """Refresh only public market/profile fields when their cache is stale.

    The expensive issuer-document and financial-statement sweep remains a
    scheduled/manual job. This small refresh is suitable for a serverless
    request and ensures the names, tickers and leaders follow KASE data.
    """
    if settings.KASE_DATA_MODE == "mock":
        return {"status": "disabled_in_mock", "refreshed": False}

    max_age = timedelta(seconds=settings.STOCK_MARKET_REFRESH_SECONDS)

    def latest_check() -> datetime | None:
        return _utc(session.scalar(select(func.max(Stock.last_checked_at))))

    now = datetime.now(timezone.utc)
    checked_at = latest_check()
    if checked_at is not None and now - checked_at <= max_age:
        return {
            "status": "fresh",
            "refreshed": False,
            "checked_at": checked_at.isoformat(),
        }

    async with _refresh_lock:
        checked_at = latest_check()
        now = datetime.now(timezone.utc)
        if checked_at is not None and now - checked_at <= max_age:
            return {
                "status": "fresh",
                "refreshed": False,
                "checked_at": checked_at.isoformat(),
            }
        try:
            stats = await KaseStockCatalogCollector(session).collect(deep=False)
        except Exception as exc:
            session.rollback()
            return {
                "status": "stale_error",
                "refreshed": False,
                "checked_at": checked_at.isoformat() if checked_at else None,
                "error": str(exc),
            }
        return {
            "status": "updated",
            "refreshed": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "collector": stats,
        }


__all__ = ["ensure_fresh_stock_market"]
