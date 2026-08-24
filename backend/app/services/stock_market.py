"""Read-only freshness metadata for stored equity market data.

External collection belongs to scheduled or explicit refresh jobs.  Client
reads must never wait for KASE, especially on a serverless cold start.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.stock import Stock


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def stored_stock_market_status(session: Session) -> dict:
    """Describe the newest validated DB snapshot without doing network I/O."""
    checked_at = _utc(session.scalar(select(func.max(Stock.last_checked_at))))
    return {
        "status": "stored" if checked_at is not None else "not_collected",
        "refreshed": False,
        "checked_at": checked_at.isoformat() if checked_at else None,
        "served_at": datetime.now(timezone.utc).isoformat(),
        "refresh_mode": "background_or_manual",
    }


__all__ = ["stored_stock_market_status"]
