"""Continuous monitoring after the backfill.

The transition the spec asks for:

    two-year backfill -> latest historical observation -> 10-minute monitoring
    -> future history

There is no seam to reconcile, because live monitoring writes into the same
``market_observations`` table with the same fingerprint rule. If the final
backfilled point and the first monitored point describe the same reading, the
fingerprint matches and the second write is a no-op.

Delisted instruments keep everything they ever had; they simply stop being
polled on the fast cadence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.forecast.calendar import kase_date
from app.models.history import MarketObservation
from app.models.instrument import Instrument, SHARE_INSTRUMENT_TYPES
from app.models.stock import Stock, StockQuote
from app.services.backfill.records import ObservationRecord, STATUS_TRADED
from app.services.backfill.store import HistoryStore
from app.services.backfill.validate import validate_observations
from app.services.backfill.window import is_market_day

logger = get_logger(__name__)

MONITORING_SOURCE = "kase_public_website"
PARSER_VERSION = "kase-monitor-v1"


class MonitoringService:
    """Turns the freshest quote we hold into a permanent observation."""

    def __init__(self, session: Session):
        self.session = session
        self.store = HistoryStore(session, parser_version=PARSER_VERSION)

    def active_instruments(self) -> list[tuple[Instrument, Stock]]:
        rows = self.session.execute(
            select(Instrument, Stock)
            .join(Stock, Stock.instrument_id == Instrument.id)
            .where(
                Instrument.instrument_type.in_(SHARE_INSTRUMENT_TYPES),
                Instrument.is_active.is_(True),
            )
        ).all()
        return [(instrument, stock) for instrument, stock in rows]

    def observation_from_quote(self, quote: StockQuote) -> ObservationRecord:
        # The database hands back naive timestamps; history is always stored in
        # UTC, so the reading is normalised before anything else looks at it.
        observed_at = quote.timestamp
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        price = quote.last if quote.last is not None else quote.close
        traded = any(
            value is not None for value in (price, quote.volume, quote.turnover)
        )
        return ObservationRecord(
            observed_at=observed_at,
            price=price,
            bid=quote.bid,
            ask=quote.ask,
            bid_volume=quote.bid_volume,
            ask_volume=quote.ask_volume,
            volume=quote.volume,
            turnover=quote.turnover,
            trade_count=quote.number_of_trades,
            open=quote.open,
            high=quote.high,
            low=quote.low,
            close=quote.close,
            previous_close=quote.previous_close,
            status=STATUS_TRADED if traded else "no_trade",
            source=quote.source or MONITORING_SOURCE,
            source_url=quote.source_url,
            source_timestamp=quote.source_timestamp or observed_at,
            parser_version=PARSER_VERSION,
            data_mode=quote.data_mode,
            trading_date=kase_date(observed_at),
        )

    def record_latest(self, instrument: Instrument, stock: Stock) -> dict:
        """Promote the newest quote we hold into permanent history."""
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=settings.MONITORING_INTERVAL_SECONDS * 6
        )
        quote = self.session.execute(
            select(StockQuote)
            .where(StockQuote.stock_id == stock.id, StockQuote.timestamp >= cutoff)
            .order_by(StockQuote.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()
        if quote is None:
            return {"ticker": instrument.ticker, "stored": 0, "reason": "no recent quote"}

        record = self.observation_from_quote(quote)
        outcome = validate_observations(
            [record],
            expected_ticker=instrument.ticker,
            reference_price=self.store.last_validated_price(instrument.id),
        )
        if outcome.rejections:
            self.store.record_anomalies(
                instrument_id=instrument.id,
                ticker=instrument.ticker,
                job_type="monitoring",
                rejections=outcome.rejections,
                source=record.source,
                source_url=record.source_url,
            )
        if not outcome.accepted:
            return {"ticker": instrument.ticker, "stored": 0, "reason": "rejected"}

        result = self.store.save_observations(instrument.id, outcome.accepted)
        if result["created"]:
            day = record.trading_date
            self.store.rebuild_daily_snapshots(instrument.id, start=day, end=day)
        return {"ticker": instrument.ticker, **result}

    async def observe_active(self) -> dict:
        """One monitoring pass over every active stock."""
        today = kase_date(datetime.now(timezone.utc))
        results = []
        for instrument, stock in self.active_instruments():
            try:
                results.append(self.record_latest(instrument, stock))
            except Exception as exc:  # one bad instrument must not stop the pass
                logger.warning("monitoring failed for %s: %s", instrument.ticker, exc)
                results.append({"ticker": instrument.ticker, "stored": 0, "error": str(exc)})
        self.session.commit()
        return {
            "instruments": len(results),
            "observations_created": sum(r.get("created", 0) for r in results),
            "duplicates": sum(r.get("duplicates", 0) for r in results),
            "market_day": is_market_day(today),
            "interval_seconds": settings.MONITORING_INTERVAL_SECONDS,
        }


__all__ = ["MONITORING_SOURCE", "MonitoringService"]
