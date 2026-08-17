"""Operational view of the historical backfill.

Answers the only questions that matter while a two-year crawl is running: how
much of the universe is done, how much history actually exists, and what the
site refused to give us.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_session
from app.models.history import (
    BackfillCheckpoint,
    DailyMarketSnapshot,
    DividendEvent,
    FinancialReportRelease,
    HistoricalCoverage,
    HistoricalTrade,
    IngestionAnomaly,
    MarketObservation,
)
from app.models.incremental import KaseNewsItem
from app.models.instrument import Instrument
from app.models.stock import CorporateAction
from app.services.backfill.queue import JOB_MARKET_HISTORY
from app.services.backfill.window import backfill_window

router = APIRouter()


@router.get("/admin/backfill/status")
def backfill_status(
    session: Session = Depends(get_session),
    _: bool = Depends(require_admin),
) -> dict:
    def _count(model, *where) -> int:
        stmt = select(func.count()).select_from(model)
        for clause in where:
            stmt = stmt.where(clause)
        return session.execute(stmt).scalar_one()

    checkpoint_counts = {
        status: count
        for status, count in session.execute(
            select(BackfillCheckpoint.status, func.count(BackfillCheckpoint.id))
            .where(BackfillCheckpoint.job_type == JOB_MARKET_HISTORY)
            .group_by(BackfillCheckpoint.status)
        ).all()
    }
    coverage_counts = {
        status: count
        for status, count in session.execute(
            select(HistoricalCoverage.status, func.count(HistoricalCoverage.id))
            .group_by(HistoricalCoverage.status)
        ).all()
    }
    bounds = session.execute(
        select(func.min(MarketObservation.observed_at), func.max(MarketObservation.observed_at))
    ).one()

    total_stocks = _count(Instrument, Instrument.instrument_type == "stock")
    active_stocks = _count(
        Instrument, Instrument.instrument_type == "stock", Instrument.is_active.is_(True)
    )

    return {
        "window": backfill_window().to_dict(),
        "total_stocks": total_stocks,
        "active_stocks": active_stocks,
        "queued": checkpoint_counts.get("queued", 0),
        "processing": checkpoint_counts.get("processing", 0),
        "completed": coverage_counts.get("complete", 0),
        "partial": coverage_counts.get("partial", 0),
        "unavailable": coverage_counts.get("unavailable", 0),
        "failed": checkpoint_counts.get("failed", 0) + coverage_counts.get("failed", 0),
        "blocked": checkpoint_counts.get("blocked", 0),
        "checkpoints": checkpoint_counts,
        "market_observations": _count(MarketObservation),
        "daily_snapshots": _count(DailyMarketSnapshot),
        "historical_trades": _count(HistoricalTrade),
        "financial_reports": _count(FinancialReportRelease),
        "news": _count(KaseNewsItem),
        "corporate_actions": _count(CorporateAction) + _count(DividendEvent),
        "dividend_events": _count(DividendEvent),
        "anomalies": _count(IngestionAnomaly, IngestionAnomaly.resolved.is_(False)),
        "oldest_observation": bounds[0].isoformat() if bounds[0] else None,
        "latest_observation": bounds[1].isoformat() if bounds[1] else None,
    }


@router.get("/admin/backfill/anomalies")
def backfill_anomalies(
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
    _: bool = Depends(require_admin),
) -> dict:
    """Parses that were refused, with the raw payload that was rejected."""
    rows = list(
        session.execute(
            select(IngestionAnomaly)
            .order_by(IngestionAnomaly.created_at.desc())
            .limit(limit)
        ).scalars()
    )
    return {
        "count": len(rows),
        "anomalies": [
            {
                "id": row.id,
                "ticker": row.ticker,
                "job_type": row.job_type,
                "kind": row.kind,
                "severity": row.severity,
                "message": row.message,
                "source_url": row.source_url,
                "parser_version": row.parser_version,
                "detected_at": row.created_at.isoformat() if row.created_at else None,
                "payload": row.payload,
            }
            for row in rows
        ],
    }
