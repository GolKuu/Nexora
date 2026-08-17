"""Idempotent, append-only storage of collected history.

Running the two-year backfill five times must leave the database in the same
state as running it once. That is enforced by fingerprints and unique
constraints, not by hoping the crawl is deterministic.

Daily snapshots are *derived* from stored observations, and only from what was
actually observed: a day with a single price keeps NULL open/high/low, and a day
with no trade is stored as ``no_trade`` rather than as a repeat of yesterday.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.forecast.calendar import kase_date
from app.models.history import (
    DailyMarketSnapshot,
    DividendEvent,
    FinancialReportRelease,
    HistoricalCorrection,
    HistoricalTrade,
    IngestionAnomaly,
    MarketObservation,
)
from app.services.backfill.records import (
    DividendRecord,
    ObservationRecord,
    ReportRecord,
    STATUS_NO_TRADE,
    STATUS_TRADED,
    TradeRecord,
)
from app.services.backfill.validate import Rejection
from app.services.backfill.window import is_market_day

PARSER_VERSION = "kase-backfill-v1"


class HistoryStore:
    """Every write in the backfill goes through here."""

    def __init__(self, session: Session, *, parser_version: str = PARSER_VERSION):
        self.session = session
        self.parser_version = parser_version

    # -- observations -----------------------------------------------------

    def existing_fingerprints(self, instrument_id: int) -> set[str]:
        rows = self.session.execute(
            select(MarketObservation.fingerprint).where(
                MarketObservation.instrument_id == instrument_id
            )
        ).scalars()
        return set(rows)

    def save_observations(
        self, instrument_id: int, records: list[ObservationRecord]
    ) -> dict:
        """Insert what is new; silently skip what we already hold."""
        known = self.existing_fingerprints(instrument_id)
        created = 0
        duplicates = 0
        batch: set[str] = set()
        for record in records:
            fingerprint = record.fingerprint()
            if fingerprint in known or fingerprint in batch:
                duplicates += 1
                continue
            batch.add(fingerprint)
            self.session.add(
                MarketObservation(
                    instrument_id=instrument_id,
                    observed_at=record.observed_at,
                    trading_date=record.trading_date or kase_date(record.observed_at),
                    price=record.price,
                    bid=record.bid,
                    ask=record.ask,
                    bid_volume=record.bid_volume,
                    ask_volume=record.ask_volume,
                    spread=record.spread,
                    volume=record.volume,
                    turnover=record.turnover,
                    trade_count=record.trade_count,
                    open=record.open,
                    high=record.high,
                    low=record.low,
                    close=record.close,
                    previous_close=record.previous_close,
                    status=record.status,
                    data_mode=record.data_mode,
                    parser_version=record.parser_version or self.parser_version,
                    fingerprint=fingerprint,
                    source=record.source,
                    source_url=record.source_url,
                    source_timestamp=record.source_timestamp or record.observed_at,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            created += 1
        self.session.flush()
        return {"created": created, "duplicates": duplicates, "received": len(records)}

    def save_trades(self, instrument_id: int, records: list[TradeRecord]) -> dict:
        known = set(
            self.session.execute(
                select(HistoricalTrade.fingerprint).where(
                    HistoricalTrade.instrument_id == instrument_id
                )
            ).scalars()
        )
        created = 0
        duplicates = 0
        batch: set[str] = set()
        for record in records:
            fingerprint = record.fingerprint()
            if fingerprint in known or fingerprint in batch:
                duplicates += 1
                continue
            batch.add(fingerprint)
            value = record.trade_value
            if value is None and record.quantity is not None:
                value = record.price * record.quantity
            self.session.add(
                HistoricalTrade(
                    instrument_id=instrument_id,
                    trade_id=record.trade_id,
                    trade_timestamp=record.trade_timestamp,
                    trading_date=kase_date(record.trade_timestamp),
                    price=record.price,
                    quantity=record.quantity,
                    trade_value=value,
                    fingerprint=fingerprint,
                    parser_version=record.parser_version or self.parser_version,
                    data_mode=record.data_mode,
                    source=record.source,
                    source_url=record.source_url,
                    source_timestamp=record.source_timestamp or record.trade_timestamp,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            created += 1
        self.session.flush()
        return {"created": created, "duplicates": duplicates, "received": len(records)}

    def save_dividends(self, instrument_id: int, records: list[DividendRecord]) -> dict:
        known = set(
            self.session.execute(
                select(DividendEvent.fingerprint).where(
                    DividendEvent.instrument_id == instrument_id
                )
            ).scalars()
        )
        created = 0
        duplicates = 0
        for record in records:
            fingerprint = record.fingerprint()
            if fingerprint in known:
                duplicates += 1
                continue
            known.add(fingerprint)
            self.session.add(
                DividendEvent(
                    instrument_id=instrument_id,
                    announcement_date=record.announcement_date,
                    ex_date=record.ex_date,
                    record_date=record.record_date,
                    payment_date=record.payment_date,
                    amount_per_share=record.amount_per_share,
                    currency=record.currency,
                    status=record.status,
                    fingerprint=fingerprint,
                    parser_version=record.parser_version or self.parser_version,
                    source=record.source,
                    source_url=record.source_url,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            created += 1
        self.session.flush()
        return {"created": created, "duplicates": duplicates, "received": len(records)}

    def save_reports(self, instrument_id: int, records: list[ReportRecord]) -> dict:
        """Store report releases. A restatement is a new version, never an edit."""
        created = 0
        duplicates = 0
        for record in records:
            existing = list(
                self.session.execute(
                    select(FinancialReportRelease).where(
                        FinancialReportRelease.instrument_id == instrument_id,
                        FinancialReportRelease.reporting_period == record.reporting_period,
                    )
                ).scalars()
            )
            if any(row.document_hash == record.document_hash for row in existing):
                duplicates += 1
                continue
            self.session.add(
                FinancialReportRelease(
                    instrument_id=instrument_id,
                    reporting_period=record.reporting_period,
                    period_type=record.period_type,
                    publication_date=record.publication_date,
                    available_at=record.available_at,
                    document_url=record.document_url,
                    document_hash=record.document_hash,
                    version=len(existing) + 1,
                    is_restatement=bool(existing),
                    title=record.title,
                    parser_version=record.parser_version or self.parser_version,
                    source=record.source,
                    source_url=record.document_url,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            created += 1
        self.session.flush()
        return {"created": created, "duplicates": duplicates, "received": len(records)}

    # -- anomalies and corrections ---------------------------------------

    def record_anomalies(
        self,
        *,
        instrument_id: int | None,
        ticker: str | None,
        job_type: str,
        rejections: list[Rejection],
        source: str | None = None,
        source_url: str | None = None,
        severity: str = "high",
    ) -> int:
        for rejection in rejections:
            self.session.add(
                IngestionAnomaly(
                    instrument_id=instrument_id,
                    ticker=ticker,
                    job_type=job_type,
                    kind=rejection.kind,
                    severity=severity,
                    message=rejection.message,
                    payload=rejection.payload,
                    source=source,
                    source_url=source_url,
                    parser_version=self.parser_version,
                )
            )
        self.session.flush()
        return len(rejections)

    def record_correction(
        self,
        *,
        instrument_id: int,
        record_type: str,
        record_id: int | None,
        field: str,
        original_value,
        corrected_value,
        effective_date: date | None = None,
        source: str | None = None,
        source_url: str | None = None,
        reason: str | None = None,
    ) -> HistoricalCorrection:
        correction = HistoricalCorrection(
            instrument_id=instrument_id,
            record_type=record_type,
            record_id=record_id,
            field=field,
            original_value=None if original_value is None else str(original_value),
            corrected_value=None if corrected_value is None else str(corrected_value),
            effective_date=effective_date,
            detected_at=datetime.now(timezone.utc),
            source=source,
            source_url=source_url,
            reason=reason,
        )
        self.session.add(correction)
        self.session.flush()
        return correction

    # -- daily aggregation ------------------------------------------------

    def rebuild_daily_snapshots(
        self, instrument_id: int, *, start: date | None = None, end: date | None = None
    ) -> dict:
        """Aggregate stored observations into one row per trading day.

        Rules that keep this factual:

        * OHLC comes from real observations. A day whose only evidence is one
          price gets ``close`` and nothing else - no flat candle.
        * A day observed as ``no_trade`` is stored as ``no_trade``; the previous
          close is never carried forward into it.
        * Days never observed at all get no row, and are counted as missing
          coverage instead.
        """
        stmt = select(MarketObservation).where(
            MarketObservation.instrument_id == instrument_id
        )
        if start is not None:
            stmt = stmt.where(MarketObservation.trading_date >= start)
        if end is not None:
            stmt = stmt.where(MarketObservation.trading_date <= end)
        observations = list(
            self.session.execute(stmt.order_by(MarketObservation.observed_at)).scalars()
        )
        if not observations:
            return {"days": 0, "created": 0, "updated": 0}

        by_day: dict[date, list[MarketObservation]] = defaultdict(list)
        for observation in observations:
            day = observation.trading_date or kase_date(observation.observed_at)
            by_day[day].append(observation)

        existing = {
            row.trading_date: row
            for row in self.session.execute(
                select(DailyMarketSnapshot).where(
                    DailyMarketSnapshot.instrument_id == instrument_id
                )
            ).scalars()
        }

        created = updated = 0
        for day, rows in sorted(by_day.items()):
            summary = _summarise_day(day, rows)
            snapshot = existing.get(day)
            if snapshot is None:
                snapshot = DailyMarketSnapshot(instrument_id=instrument_id, trading_date=day)
                self.session.add(snapshot)
                existing[day] = snapshot
                created += 1
            else:
                updated += 1
            for field, value in summary.items():
                setattr(snapshot, field, value)
        self.session.flush()
        return {"days": len(by_day), "created": created, "updated": updated}

    # -- reads used by coverage and charts --------------------------------

    def observation_bounds(self, instrument_id: int) -> tuple[datetime | None, datetime | None]:
        row = self.session.execute(
            select(
                func.min(MarketObservation.observed_at),
                func.max(MarketObservation.observed_at),
            ).where(MarketObservation.instrument_id == instrument_id)
        ).one()
        return row[0], row[1]

    def last_validated_price(self, instrument_id: int) -> float | None:
        """The most recent stored price, used as the sanity reference."""
        row = self.session.execute(
            select(MarketObservation.price)
            .where(
                MarketObservation.instrument_id == instrument_id,
                MarketObservation.price.is_not(None),
                MarketObservation.status == STATUS_TRADED,
            )
            .order_by(MarketObservation.observed_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        return row


def _summarise_day(day: date, rows: list[MarketObservation]) -> dict:
    """Build one daily row from the observations that actually exist."""
    traded = [r for r in rows if r.status == STATUS_TRADED]
    prices = [r.price for r in traded if r.price is not None]
    # A source that publishes daily OHLC directly is preferred over anything
    # we could reconstruct from intraday samples.
    published = next((r for r in traded if r.open is not None and r.close is not None), None)

    volume = sum(r.volume for r in traded if r.volume is not None) or None
    turnover = sum(r.turnover for r in traded if r.turnover is not None) or None
    trade_count = sum(r.trade_count for r in traded if r.trade_count is not None) or None
    quotes = [r for r in rows if r.bid is not None or r.ask is not None]

    if published is not None:
        open_, high, low, close = published.open, published.high, published.low, published.close
        quality = "full"
    elif len(prices) >= 2:
        open_, close = prices[0], prices[-1]
        high, low = max(prices), min(prices)
        quality = "full"
    elif len(prices) == 1:
        # One price is a close, not a candle. Manufacturing open == high == low
        # here would be inventing three numbers we never saw.
        open_ = high = low = None
        close = prices[0]
        quality = "single_price"
    else:
        open_ = high = low = close = None
        quality = "quote_only" if quotes else "no_trade"

    if traded:
        status = STATUS_TRADED
    elif not is_market_day(day):
        status = "market_closed"
    elif rows:
        status = rows[-1].status if rows[-1].status != STATUS_TRADED else STATUS_NO_TRADE
    else:  # pragma: no cover - by construction rows is non-empty
        status = "data_unavailable"

    if status != STATUS_TRADED and quality == "no_trade" and not quotes:
        quality = "no_trade"

    return {
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "turnover": turnover, "trade_count": trade_count,
        "bid_close": quotes[-1].bid if quotes else None,
        "ask_close": quotes[-1].ask if quotes else None,
        "first_observation_at": rows[0].observed_at,
        "last_observation_at": rows[-1].observed_at,
        "observation_count": len(rows),
        "coverage_quality": quality,
        "status": status,
        "data_mode": rows[-1].data_mode,
        "source": rows[-1].source,
        "source_url": rows[-1].source_url,
        "source_timestamp": rows[-1].source_timestamp,
        "fetched_at": datetime.now(timezone.utc),
    }


__all__ = ["HistoryStore", "PARSER_VERSION"]
