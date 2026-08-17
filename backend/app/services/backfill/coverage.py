"""Measuring what we actually got.

The UI must never imply a full two years of history when KASE exposed eight
months of it. Coverage is computed from stored rows against the requested
window, rounded *down*, and published through the API next to every chart.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.forecast.calendar import kase_date
from app.models.history import (
    DailyMarketSnapshot,
    DividendEvent,
    FinancialReportRelease,
    HistoricalCoverage,
    HistoricalTrade,
    MarketObservation,
)
from app.models.incremental import KaseNewsItem
from app.models.instrument import Instrument
from app.services.backfill.window import BackfillWindow, expected_market_days

COMPLETE = "complete"
PARTIAL = "partial"
UNAVAILABLE = "unavailable"
FAILED = "failed"

#: Below this share of expected trading days the history is "partial", however
#: good the data we do have looks.
COMPLETE_THRESHOLD = 0.95


def _ratio(covered: int | None, expected: int | None) -> float | None:
    if not expected:
        return None
    return round(min((covered or 0) / expected, 1.0), 4)


class CoverageService:
    def __init__(self, session: Session):
        self.session = session

    def measure(
        self,
        instrument: Instrument,
        window: BackfillWindow,
        *,
        job_type: str = "all",
        status: str | None = None,
        details: dict | None = None,
    ) -> HistoricalCoverage:
        instrument_id = instrument.id
        start_day, end_day = window.start_date, window.end_date
        expected = expected_market_days(start_day, end_day)

        covered = self.session.execute(
            select(func.count(func.distinct(DailyMarketSnapshot.trading_date))).where(
                DailyMarketSnapshot.instrument_id == instrument_id,
                DailyMarketSnapshot.trading_date >= start_day,
                DailyMarketSnapshot.trading_date <= end_day,
            )
        ).scalar_one()

        bounds = self.session.execute(
            select(
                func.min(MarketObservation.observed_at),
                func.max(MarketObservation.observed_at),
            ).where(MarketObservation.instrument_id == instrument_id)
        ).one()

        trade_days = self.session.execute(
            select(func.count(func.distinct(HistoricalTrade.trading_date))).where(
                HistoricalTrade.instrument_id == instrument_id,
                HistoricalTrade.trading_date >= start_day,
                HistoricalTrade.trading_date <= end_day,
            )
        ).scalar_one()

        quote_days = self.session.execute(
            select(func.count(func.distinct(MarketObservation.trading_date))).where(
                MarketObservation.instrument_id == instrument_id,
                MarketObservation.trading_date >= start_day,
                MarketObservation.trading_date <= end_day,
                MarketObservation.bid.is_not(None),
            )
        ).scalar_one()

        reports = self.session.execute(
            select(func.count(FinancialReportRelease.id)).where(
                FinancialReportRelease.instrument_id == instrument_id,
                FinancialReportRelease.reporting_period >= start_day,
            )
        ).scalar_one()

        dividends = self.session.execute(
            select(func.count(DividendEvent.id)).where(
                DividendEvent.instrument_id == instrument_id
            )
        ).scalar_one()

        news = self.session.execute(
            select(func.count(KaseNewsItem.id)).where(
                KaseNewsItem.ticker == instrument.ticker,
                KaseNewsItem.publication_date >= window.start,
            )
        ).scalar_one()

        # Quarterly reporting: eight periods over two years is full coverage.
        expected_reports = max(window.years * 4, 1)

        row = self.session.execute(
            select(HistoricalCoverage).where(
                HistoricalCoverage.instrument_id == instrument_id,
                HistoricalCoverage.job_type == job_type,
            )
        ).scalar_one_or_none()
        if row is None:
            row = HistoricalCoverage(instrument_id=instrument_id, job_type=job_type)
            self.session.add(row)

        row.requested_start = window.start
        row.requested_end = window.end
        row.actual_market_start = bounds[0]
        row.actual_market_end = bounds[1]
        row.market_days_expected = expected
        row.market_days_covered = covered
        row.trade_history_coverage = _ratio(trade_days, expected)
        row.quote_history_coverage = _ratio(quote_days, expected)
        row.financial_history_coverage = _ratio(reports, expected_reports)
        row.news_history_coverage = None if news == 0 else 1.0
        row.corporate_action_coverage = None if dividends == 0 else 1.0
        row.last_backfilled_at = datetime.now(timezone.utc)
        row.status = status or self._status(covered, expected)
        row.details = {
            **(details or {}),
            "daily_snapshots": covered,
            "trade_days": trade_days,
            "quote_days": quote_days,
            "reports": reports,
            "dividend_events": dividends,
            "news_items": news,
        }
        self.session.flush()
        return row

    def _status(self, covered: int, expected: int) -> str:
        if expected == 0 or covered == 0:
            return UNAVAILABLE
        return COMPLETE if covered / expected >= COMPLETE_THRESHOLD else PARTIAL

    def get(self, instrument_id: int, *, job_type: str = "all") -> HistoricalCoverage | None:
        return self.session.execute(
            select(HistoricalCoverage).where(
                HistoricalCoverage.instrument_id == instrument_id,
                HistoricalCoverage.job_type == job_type,
            )
        ).scalar_one_or_none()

    @staticmethod
    def to_dict(row: HistoricalCoverage | None) -> dict:
        """The payload the UI uses to avoid overstating what it can draw."""
        if row is None:
            return {
                "status": UNAVAILABLE,
                "market_days_expected": None,
                "market_days_covered": 0,
                "completeness": None,
                "requested_start": None,
                "requested_end": None,
                "actual_market_start": None,
                "actual_market_end": None,
                "last_backfilled_at": None,
            }
        completeness = _ratio(row.market_days_covered, row.market_days_expected)
        return {
            "status": row.status,
            "requested_start": row.requested_start.isoformat() if row.requested_start else None,
            "requested_end": row.requested_end.isoformat() if row.requested_end else None,
            "actual_market_start": row.actual_market_start.isoformat()
            if row.actual_market_start else None,
            "actual_market_end": row.actual_market_end.isoformat()
            if row.actual_market_end else None,
            "market_days_expected": row.market_days_expected,
            "market_days_covered": row.market_days_covered,
            "completeness": completeness,
            "trade_history_coverage": row.trade_history_coverage,
            "quote_history_coverage": row.quote_history_coverage,
            "financial_history_coverage": row.financial_history_coverage,
            "news_history_coverage": row.news_history_coverage,
            "corporate_action_coverage": row.corporate_action_coverage,
            "last_backfilled_at": row.last_backfilled_at.isoformat()
            if row.last_backfilled_at else None,
            "details": row.details,
        }


__all__ = ["COMPLETE", "CoverageService", "FAILED", "PARTIAL", "UNAVAILABLE"]
