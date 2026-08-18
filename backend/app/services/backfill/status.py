"""Per-instrument history status.

The honest answer to "how much history do we have for this stock?" - stated in
trading days, with the gaps counted rather than smoothed over.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.history import (
    BackfillCheckpoint,
    DailyMarketSnapshot,
    DividendEvent,
    FinancialReportRelease,
    HistoricalTrade,
    IngestionAnomaly,
    MarketObservation,
)
from app.models.incremental import KaseNewsItem
from app.models.instrument import Instrument
from app.models.stock import CorporateAction, Stock
from app.services.backfill.coverage import CoverageService
from app.services.backfill.queue import JOB_MARKET_HISTORY
from app.services.backfill.records import STATUS_NO_TRADE, STATUS_TRADED
from app.services.backfill.window import backfill_window


def stock_history_coverage(session: Session, instrument: Instrument) -> dict:
    window = backfill_window()
    coverage = CoverageService(session).get(instrument.id)

    def _count(model, *where) -> int:
        stmt = select(func.count()).select_from(model).where(
            model.instrument_id == instrument.id
        )
        for clause in where:
            stmt = stmt.where(clause)
        return session.execute(stmt).scalar_one()

    bounds = session.execute(
        select(
            func.min(MarketObservation.observed_at),
            func.max(MarketObservation.observed_at),
        ).where(MarketObservation.instrument_id == instrument.id)
    ).one()

    checkpoint = session.execute(
        select(BackfillCheckpoint).where(
            BackfillCheckpoint.job_type == JOB_MARKET_HISTORY,
            BackfillCheckpoint.instrument_id == instrument.id,
        )
    ).scalar_one_or_none()

    traded_days = _count(DailyMarketSnapshot, DailyMarketSnapshot.status == STATUS_TRADED)
    quiet_days = _count(DailyMarketSnapshot, DailyMarketSnapshot.status == STATUS_NO_TRADE)

    stock_row = session.execute(
        select(Stock).where(Stock.instrument_id == instrument.id)
    ).scalar_one_or_none()

    news_items = session.execute(
        select(func.count(KaseNewsItem.id)).where(KaseNewsItem.ticker == instrument.ticker)
    ).scalar_one()
    corporate_actions = session.execute(
        select(func.count(CorporateAction.id))
        .join(Stock, Stock.id == CorporateAction.stock_id)
        .where(Stock.instrument_id == instrument.id)
    ).scalar_one()

    return {
        "instrument": {
            "id": instrument.id,
            "ticker": instrument.ticker,
            "isin": instrument.isin,
            "is_active": instrument.is_active,
            # A delisted share keeps everything it ever had; this is the date it
            # stopped trading, not a reason to hide the history.
            "delisted_at": stock_row.delisted_at.isoformat()
            if stock_row is not None and stock_row.delisted_at else None,
            "kase_url": instrument.kase_url,
        },
        "window": window.to_dict(),
        "coverage": CoverageService.to_dict(coverage),
        "stored": {
            "market_observations": _count(MarketObservation),
            "daily_snapshots": _count(DailyMarketSnapshot),
            "traded_days": traded_days,
            "no_trade_days": quiet_days,
            "historical_trades": _count(HistoricalTrade),
            "dividend_events": _count(DividendEvent),
            "financial_reports": _count(FinancialReportRelease),
            "news": news_items,
            "corporate_actions": corporate_actions,
            "anomalies": _count(IngestionAnomaly, IngestionAnomaly.resolved.is_(False)),
        },
        "oldest_observation": bounds[0].isoformat() if bounds[0] else None,
        "latest_observation": bounds[1].isoformat() if bounds[1] else None,
        "backfill": {
            "status": checkpoint.status if checkpoint else "not_queued",
            "priority": checkpoint.priority if checkpoint else None,
            "attempts": checkpoint.attempts if checkpoint else 0,
            "last_processed_timestamp": checkpoint.last_processed_timestamp.isoformat()
            if checkpoint and checkpoint.last_processed_timestamp else None,
            "last_error": checkpoint.last_error if checkpoint else None,
            "next_attempt_at": checkpoint.next_attempt_at.isoformat()
            if checkpoint and checkpoint.next_attempt_at else None,
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["stock_history_coverage"]
