"""One price, one series, one answer - for every screen in the product.

Prices used to arrive on two tracks. ``stock_quotes`` fed the list, the card,
the metrics, the scores and the forecast; ``market_observations`` fed the
history chart. Both were real, neither was wrong, and they disagreed: the chart
included days the backfill had recovered from kase.kz and reflected published
corrections, while the badge above it showed whatever the last catalogue
snapshot happened to say.

This module makes the permanent history the single canonical source:

* ``market_observations`` / ``daily_market_snapshots`` answer every read;
* ``stock_quotes`` stays an ingestion staging table, promoted into history at
  write time (see ``MonitoringService.promote_quotes``);
* a quote is read directly only when the instrument has *no* history at all, and
  that case is labelled ``origin="quote"`` rather than quietly blended in.

Nothing here invents a price. If the history holds nothing and no quote exists,
the answer is ``None`` - not a stale number carried forward.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.forecast.calendar import kase_date
from app.models.history import DailyMarketSnapshot, MarketObservation
from app.models.stock import Stock, StockQuote
from app.services.backfill.records import STATUS_TRADED


@dataclass(frozen=True, slots=True)
class CanonicalPrice:
    """The current market picture for one instrument, from one source."""

    price: float | None
    bid: float | None
    ask: float | None
    volume: float | None
    turnover: float | None
    trade_count: int | None
    observed_at: datetime | None
    trading_date: date | None
    status: str
    source: str | None
    source_url: str | None
    data_mode: str | None
    #: ``history`` (canonical) or ``quote`` (no history stored yet).
    origin: str

    @property
    def spread_percent(self) -> float | None:
        if not self.bid or not self.ask:
            return None
        mid = (self.ask + self.bid) / 2
        return None if not mid else (self.ask - self.bid) / mid

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "volume": self.volume,
            "turnover": self.turnover,
            "trade_count": self.trade_count,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "trading_date": self.trading_date.isoformat() if self.trading_date else None,
            "status": self.status,
            "source": self.source,
            "source_url": self.source_url,
            "data_mode": self.data_mode,
            "origin": self.origin,
        }


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every timestamp we publish is UTC."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class PriceService:
    def __init__(self, session: Session):
        self.session = session

    # -- latest -----------------------------------------------------------

    def latest(self, instrument_id: int, *, stock_id: int | None = None) -> CanonicalPrice | None:
        """The most recent factual reading, from the permanent history.

        Superseded readings are skipped, so a value KASE later corrected is
        never the one shown.
        """
        observation = self.session.execute(
            select(MarketObservation)
            .where(
                MarketObservation.instrument_id == instrument_id,
                MarketObservation.superseded_at.is_(None),
            )
            .order_by(MarketObservation.observed_at.desc(), MarketObservation.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if observation is not None:
            return CanonicalPrice(
                price=observation.price if observation.price is not None else observation.close,
                bid=observation.bid,
                ask=observation.ask,
                volume=observation.volume,
                turnover=observation.turnover,
                trade_count=observation.trade_count,
                observed_at=_aware(observation.observed_at),
                trading_date=observation.trading_date,
                status=observation.status,
                source=observation.source,
                source_url=observation.source_url,
                data_mode=observation.data_mode,
                origin="history",
            )
        if stock_id is None:
            return None
        return self._from_quote(stock_id)

    def _from_quote(self, stock_id: int) -> CanonicalPrice | None:
        """Fallback for an instrument with no stored history yet.

        Marked ``origin="quote"`` so a caller can tell a not-yet-promoted
        reading from the canonical record instead of assuming they are equal.
        """
        quote = self.session.execute(
            select(StockQuote)
            .where(StockQuote.stock_id == stock_id)
            .order_by(StockQuote.timestamp.desc(), StockQuote.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if quote is None:
            return None
        price = quote.last if quote.last is not None else quote.close
        return CanonicalPrice(
            price=price,
            bid=quote.bid,
            ask=quote.ask,
            volume=quote.volume,
            turnover=quote.turnover,
            trade_count=quote.number_of_trades,
            observed_at=_aware(quote.timestamp),
            trading_date=None,
            status=STATUS_TRADED if price is not None else "data_unavailable",
            source=quote.source,
            source_url=quote.source_url,
            data_mode=quote.data_mode,
            origin="quote",
        )

    # -- series -----------------------------------------------------------

    def daily_snapshots(
        self,
        instrument_id: int,
        *,
        limit: int | None = None,
        start: date | None = None,
        traded_only: bool = True,
    ) -> list[DailyMarketSnapshot]:
        """Daily history, oldest first - the same rows the chart draws."""
        stmt = select(DailyMarketSnapshot).where(
            DailyMarketSnapshot.instrument_id == instrument_id
        )
        if start is not None:
            stmt = stmt.where(DailyMarketSnapshot.trading_date >= start)
        if traded_only:
            stmt = stmt.where(DailyMarketSnapshot.status == STATUS_TRADED)
        stmt = stmt.order_by(DailyMarketSnapshot.trading_date.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(reversed(list(self.session.execute(stmt).scalars())))

    def daily_closes(self, instrument_id: int, *, limit: int | None = 252) -> list[float]:
        """Closing prices for volatility, drawdown and the forecast models.

        Days with no close are simply absent: a gap in the record is a gap, and
        filling it would change every statistic computed from this list.
        """
        return [
            row.close
            for row in self.daily_snapshots(instrument_id, limit=limit)
            if row.close is not None
        ]

    def daily_series(
        self,
        instrument_id: int,
        *,
        stock_id: int | None = None,
        limit: int | None = 252,
        start: date | None = None,
    ) -> tuple[list[dict], str]:
        """Normalised daily bars plus the origin they came from.

        Returns ``(rows, origin)``. ``origin="history"`` is the canonical answer
        and matches the chart point for point. ``origin="quote"`` means this
        instrument has no stored history yet and the rows were folded from raw
        quotes - the same fallback rule as :meth:`latest`, so every reader
        degrades identically instead of each inventing its own behaviour.
        """
        snapshots = self.daily_snapshots(instrument_id, limit=limit, start=start)
        if snapshots:
            return [
                {
                    "timestamp": _aware(row.last_observation_at or row.first_observation_at),
                    "trading_date": row.trading_date,
                    "open": row.open, "high": row.high, "low": row.low, "close": row.close,
                    "bid": row.bid_close, "ask": row.ask_close,
                    "volume": row.volume, "turnover": row.turnover,
                    "trade_count": row.trade_count,
                    "status": row.status, "coverage_quality": row.coverage_quality,
                    "source": row.source, "source_url": row.source_url,
                    "data_mode": row.data_mode,
                }
                for row in snapshots
            ], "history"
        if stock_id is None:
            return [], "history"
        return self._quote_series(stock_id, limit=limit, start=start), "quote"

    def _quote_series(
        self, stock_id: int, *, limit: int | None, start: date | None
    ) -> list[dict]:
        """Raw quotes folded to one row per day, for a stock with no history yet.

        One bar per day, built only from what was sampled: a day with a single
        reading keeps a close and nothing else, exactly as the daily aggregation
        of real observations would.
        """
        stmt = select(StockQuote).where(StockQuote.stock_id == stock_id)
        if start is not None:
            stmt = stmt.where(StockQuote.timestamp >= datetime(
                start.year, start.month, start.day, tzinfo=timezone.utc
            ))
        rows = list(
            self.session.execute(
                stmt.order_by(StockQuote.timestamp, StockQuote.id)
            ).scalars()
        )
        by_day: dict[date, list[StockQuote]] = {}
        for row in rows:
            moment = _aware(row.timestamp)
            by_day.setdefault(kase_date(moment), []).append(row)

        series: list[dict] = []
        for day in sorted(by_day):
            group = by_day[day]
            prices = [q.last if q.last is not None else q.close for q in group]
            prices = [value for value in prices if value is not None]
            last = group[-1]
            series.append({
                "timestamp": _aware(last.timestamp),
                "trading_date": day,
                "open": last.open if last.open is not None else (
                    prices[0] if len(prices) > 1 else None
                ),
                "high": last.high if last.high is not None else (
                    max(prices) if len(prices) > 1 else None
                ),
                "low": last.low if last.low is not None else (
                    min(prices) if len(prices) > 1 else None
                ),
                "close": prices[-1] if prices else None,
                "bid": last.bid, "ask": last.ask,
                "volume": last.volume, "turnover": last.turnover,
                "trade_count": last.number_of_trades,
                "status": STATUS_TRADED if prices else "data_unavailable",
                "coverage_quality": "single_price" if len(prices) == 1 else (
                    "full" if prices else "no_trade"
                ),
                "source": last.source, "source_url": last.source_url,
                "data_mode": last.data_mode,
            })
        return series if limit is None else series[-limit:]

    def intraday_points(
        self, instrument_id: int, *, stock_id: int | None = None
    ) -> list[tuple[datetime, float, float | None]]:
        """Every traded reading we hold, oldest first: ``(when, price, size)``.

        Event studies measure a move minutes after a headline, so they need the
        observation-level record rather than daily bars - and it must be the
        same record the chart's intraday resolutions draw, or a reaction would
        be measured against prices the reader cannot see.
        """
        rows = list(
            self.session.execute(
                select(MarketObservation)
                .where(
                    MarketObservation.instrument_id == instrument_id,
                    MarketObservation.superseded_at.is_(None),
                    MarketObservation.status == STATUS_TRADED,
                )
                .order_by(MarketObservation.observed_at, MarketObservation.id)
            ).scalars()
        )
        if rows:
            points = [
                (_aware(row.observed_at), row.price if row.price is not None else row.close,
                 row.volume if row.volume is not None else row.turnover)
                for row in rows
            ]
            return [(when, price, size) for when, price, size in points if price is not None]
        if stock_id is None:
            return []
        quotes = self.session.execute(
            select(StockQuote)
            .where(StockQuote.stock_id == stock_id)
            .order_by(StockQuote.timestamp, StockQuote.id)
        ).scalars()
        return [
            (_aware(row.timestamp), row.close if row.close is not None else row.last,
             row.volume if row.volume is not None else row.turnover)
            for row in quotes
            if (row.close if row.close is not None else row.last) is not None
        ]

    def market_closes(self) -> list[tuple[int, date, float, str | None, datetime | None]]:
        """Daily closes across the whole equity universe, one basis for all.

        Rows are ``(stock_id, trading_date, close, sector, observed_at)``. Every
        stock contributes on the same footing: the canonical history where it
        has one, and its raw quotes only while it has none - the same fallback
        rule as :meth:`latest` and :meth:`daily_series`, so a cross-sectional
        market factor is never half-built from one source and half from another.
        """
        rows: list[tuple[int, date, float, str | None, datetime | None]] = [
            (stock_id, trading_date, float(close), sector, _aware(observed_at))
            for stock_id, trading_date, close, observed_at, sector in self.session.execute(
                select(
                    Stock.id,
                    DailyMarketSnapshot.trading_date,
                    DailyMarketSnapshot.close,
                    DailyMarketSnapshot.last_observation_at,
                    Stock.sector,
                )
                .join(Stock, Stock.instrument_id == DailyMarketSnapshot.instrument_id)
                .where(
                    DailyMarketSnapshot.close.is_not(None),
                    DailyMarketSnapshot.status == STATUS_TRADED,
                )
                .order_by(Stock.id, DailyMarketSnapshot.trading_date)
            ).all()
        ]
        covered = {stock_id for stock_id, *_ in rows}

        quote_rows = self.session.execute(
            select(
                Stock.id, StockQuote.timestamp, StockQuote.close, StockQuote.last, Stock.sector
            )
            .join(StockQuote, StockQuote.stock_id == Stock.id)
            .where(or_(StockQuote.close.is_not(None), StockQuote.last.is_not(None)))
            .order_by(Stock.id, StockQuote.timestamp, StockQuote.id)
        ).all()
        for stock_id, timestamp, close, last, sector in quote_rows:
            if stock_id in covered:
                continue  # this stock already speaks through the history
            moment = _aware(timestamp)
            rows.append((stock_id, kase_date(moment), float(close or last), sector, moment))
        return rows

    def has_history(self, instrument_id: int) -> bool:
        row = self.session.execute(
            select(MarketObservation.id)
            .where(MarketObservation.instrument_id == instrument_id)
            .limit(1)
        ).scalar_one_or_none()
        return row is not None


__all__ = ["CanonicalPrice", "PriceService"]
