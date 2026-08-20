"""Chart series built from stored factual history.

Two rules the rest of the product depends on:

* **Gaps stay gaps.** A day with no trade produces no price point (and is
  reported as ``no_trade``), never a repeat of yesterday's close. A chart that
  looks sparse is telling the truth about a thinly traded instrument.
* **Aggregation is a view, not a deletion.** Long ranges are served from daily
  or weekly buckets for performance; the underlying observations are never
  removed or downsampled in storage.

``insufficient_history`` and ``coverage`` travel with every response so the UI
can say "we have eight months" instead of drawing eight months inside a frame
labelled two years.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.forecast.calendar import kase_date
from app.models.history import (
    DailyMarketSnapshot,
    DividendEvent,
    MarketObservation,
)
from app.models.instrument import Instrument
from app.models.stock import CorporateAction, Stock
from app.services.backfill.coverage import CoverageService
from app.services.backfill.records import STATUS_TRADED
from app.services.backfill.window import expected_market_days, shift_years

#: range -> (days back, None for "everything")
RANGES: dict[str, int | None] = {
    "1d": 1,
    "5d": 5,
    "1m": 31,
    "3m": 92,
    "6m": 183,
    "1y": 366,
    "2y": 731,
    "3y": 1096,
    "5y": 1827,
    "max": None,
}

#: Ranges expressed as a whole number of calendar years. These are anchored on
#: the calendar rather than counted in days, so leap years cannot shift them.
_YEAR_RANGES: dict[str, int] = {"2y": 2, "3y": 3, "5y": 5}

RESOLUTIONS = ("auto", "10m", "1h", "1d", "1w", "1mo")

#: Which resolution ``auto`` picks. Intraday only where intraday exists.
_AUTO_RESOLUTION: dict[str, str] = {
    "1d": "10m",
    "5d": "1h",
    "1m": "1d",
    "3m": "1d",
    "6m": "1d",
    "1y": "1d",
    "2y": "1w",
    "3y": "1w",
    "5y": "1mo",
    "max": "1w",
}

_BUCKET_SECONDS = {"10m": 600, "1h": 3600}


@dataclass(slots=True)
class ChartPoint:
    timestamp: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    turnover: float | None = None
    trade_count: int | None = None
    status: str = STATUS_TRADED

    def to_dict(self) -> dict:
        return {
            "t": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "turnover": self.turnover,
            "trade_count": self.trade_count,
            "status": self.status,
        }


def resolve_range(value: str | None) -> str:
    key = (value or "1m").strip().lower()
    return key if key in RANGES else "1m"


def resolve_resolution(range_key: str, value: str | None) -> str:
    key = (value or "auto").strip().lower()
    if key not in RESOLUTIONS:
        key = "auto"
    return _AUTO_RESOLUTION.get(range_key, "1d") if key == "auto" else key


def range_start(range_key: str, *, now: datetime) -> datetime | None:
    days = RANGES.get(range_key)
    if days is None:
        return None
    years = _YEAR_RANGES.get(range_key)
    if years is not None:
        # Anchored on the calendar, so a whole-year chart lines up with the
        # window the backfill actually requested (leap years included).
        return datetime.combine(
            shift_years(kase_date(now), years), now.timetz()
        ).astimezone(timezone.utc)
    return now - timedelta(days=days)


class ChartService:
    def __init__(self, session: Session):
        self.session = session

    # -- series -----------------------------------------------------------

    def series(
        self,
        instrument: Instrument,
        *,
        range_key: str = "1m",
        resolution: str = "auto",
        now: datetime | None = None,
    ) -> dict:
        now = now or datetime.now(timezone.utc)
        range_key = resolve_range(range_key)
        resolution = resolve_resolution(range_key, resolution)
        start = range_start(range_key, now=now)

        if resolution in _BUCKET_SECONDS:
            points, source = self._intraday(instrument.id, start, resolution)
        else:
            points, source = self._daily(instrument.id, start, resolution)

        traded = [p for p in points if p.close is not None]
        coverage = CoverageService(self.session).get(instrument.id)
        expected = (
            expected_market_days(kase_date(start), kase_date(now)) if start else None
        )
        # Sufficiency is judged on stored trading days, never on the number of
        # points returned: weekly buckets are a rendering choice and must not
        # make a complete history look thin.
        insufficient = self._insufficient(
            range_key, traded, expected, start,
            traded_days=self._traded_day_count(instrument.id, start),
        )

        last_observation = self.session.execute(
            select(MarketObservation.observed_at, MarketObservation.data_mode)
            .where(
                MarketObservation.instrument_id == instrument.id,
                MarketObservation.superseded_at.is_(None),
            )
            .order_by(MarketObservation.observed_at.desc())
            .limit(1)
        ).first()

        return {
            "instrument": {
                "id": instrument.id,
                "ticker": instrument.ticker,
                "isin": instrument.isin,
                "currency": instrument.currency,
                "type": instrument.instrument_type,
                "is_active": instrument.is_active,
                "kase_url": instrument.kase_url,
            },
            "range": range_key,
            "resolution": resolution,
            "requested_start": start.isoformat() if start else None,
            "requested_end": now.isoformat(),
            "series": [point.to_dict() for point in points],
            "points": len(points),
            "traded_points": len(traded),
            "events": self.events(instrument, start=start, end=now),
            "source": source,
            "data_mode": last_observation[1] if last_observation else None,
            "last_updated": last_observation[0].isoformat() if last_observation else None,
            "coverage": CoverageService.to_dict(coverage),
            "insufficient_history": insufficient,
        }

    def _daily(
        self, instrument_id: int, start: datetime | None, resolution: str
    ) -> tuple[list[ChartPoint], str]:
        stmt = select(DailyMarketSnapshot).where(
            DailyMarketSnapshot.instrument_id == instrument_id
        )
        if start is not None:
            stmt = stmt.where(DailyMarketSnapshot.trading_date >= kase_date(start))
        rows = list(
            self.session.execute(stmt.order_by(DailyMarketSnapshot.trading_date)).scalars()
        )
        points = [
            ChartPoint(
                timestamp=datetime.combine(row.trading_date, datetime.min.time(), tzinfo=timezone.utc),
                open=row.open, high=row.high, low=row.low, close=row.close,
                volume=row.volume, turnover=row.turnover, trade_count=row.trade_count,
                status=row.status,
            )
            for row in rows
        ]
        if resolution in ("1w", "1mo"):
            points = _bucket_calendar(points, resolution)
        return points, "daily_market_snapshots"

    def _intraday(
        self, instrument_id: int, start: datetime | None, resolution: str
    ) -> tuple[list[ChartPoint], str]:
        stmt = select(MarketObservation).where(
            MarketObservation.instrument_id == instrument_id,
            # Corrected readings replace the originals on the chart; the
            # originals remain in the table for the audit trail.
            MarketObservation.superseded_at.is_(None),
        )
        if start is not None:
            stmt = stmt.where(MarketObservation.observed_at >= start)
        rows = list(
            self.session.execute(stmt.order_by(MarketObservation.observed_at)).scalars()
        )
        seconds = _BUCKET_SECONDS[resolution]
        buckets: dict[int, list[MarketObservation]] = {}
        for row in rows:
            key = int(row.observed_at.timestamp()) // seconds
            buckets.setdefault(key, []).append(row)

        points: list[ChartPoint] = []
        for key in sorted(buckets):
            group = buckets[key]
            prices = [r.price for r in group if r.price is not None and r.status == STATUS_TRADED]
            points.append(
                ChartPoint(
                    timestamp=datetime.fromtimestamp(key * seconds, tz=timezone.utc),
                    # A bucket holding one print is a close, not a candle.
                    open=prices[0] if len(prices) > 1 else None,
                    high=max(prices) if len(prices) > 1 else None,
                    low=min(prices) if len(prices) > 1 else None,
                    close=prices[-1] if prices else None,
                    volume=sum(r.volume for r in group if r.volume is not None) or None,
                    turnover=sum(r.turnover for r in group if r.turnover is not None) or None,
                    trade_count=sum(r.trade_count for r in group if r.trade_count is not None) or None,
                    status=STATUS_TRADED if prices else group[-1].status,
                )
            )
        return points, "market_observations"

    def _traded_day_count(self, instrument_id: int, start: datetime | None) -> int:
        stmt = select(func.count(DailyMarketSnapshot.id)).where(
            DailyMarketSnapshot.instrument_id == instrument_id,
            DailyMarketSnapshot.status == STATUS_TRADED,
        )
        if start is not None:
            stmt = stmt.where(DailyMarketSnapshot.trading_date >= kase_date(start))
        return self.session.execute(stmt).scalar_one()

    def _insufficient(
        self,
        range_key: str,
        traded: list[ChartPoint],
        expected: int | None,
        start: datetime | None,
        *,
        traded_days: int,
    ) -> dict:
        """Say plainly when the stored history cannot fill the requested range."""
        if not traded:
            return {
                "value": True,
                "reason": "Нет сохраненных торговых наблюдений за выбранный период.",
                "available_points": 0,
                "expected_market_days": expected,
            }
        if expected and range_key in ("1y", "2y", "3y", "5y", "max"):
            ratio = traded_days / expected
            if ratio < 0.5:
                return {
                    "value": True,
                    "reason": (
                        f"KASE раскрывает лишь часть периода: есть {traded_days} "
                        f"торговых дней из ожидаемых {expected}."
                    ),
                    "available_points": len(traded),
                    "traded_days": traded_days,
                    "expected_market_days": expected,
                    "completeness": round(ratio, 4),
                }
        first = traded[0].timestamp
        if start is not None and first > start + timedelta(days=45):
            return {
                "value": True,
                "reason": f"История начинается только с {first.date().isoformat()}.",
                "available_points": len(traded),
                "expected_market_days": expected,
                "actual_start": first.isoformat(),
            }
        return {
            "value": False,
            "available_points": len(traded),
            "traded_days": traded_days,
            "expected_market_days": expected,
        }

    # -- events -----------------------------------------------------------

    def events(
        self, instrument: Instrument, *, start: datetime | None, end: datetime
    ) -> list[dict]:
        """Dividends and corporate actions overlaid on the price series."""
        stock = self.session.execute(
            select(Stock).where(Stock.instrument_id == instrument.id)
        ).scalar_one_or_none()

        stmt = select(DividendEvent).where(DividendEvent.instrument_id == instrument.id)
        if start is not None:
            stmt = stmt.where(DividendEvent.ex_date >= kase_date(start))
        events = [
            {
                "type": "dividend",
                "date": (row.ex_date or row.payment_date or row.record_date).isoformat()
                if (row.ex_date or row.payment_date or row.record_date) else None,
                "amount_per_share": row.amount_per_share,
                "currency": row.currency,
                "status": row.status,
                "source_url": row.source_url,
            }
            for row in self.session.execute(stmt.order_by(DividendEvent.ex_date)).scalars()
        ]

        if stock is not None:
            action_stmt = select(CorporateAction).where(CorporateAction.stock_id == stock.id)
            if start is not None:
                action_stmt = action_stmt.where(CorporateAction.event_date >= kase_date(start))
            events.extend(
                {
                    "type": row.action_type,
                    "date": row.event_date.isoformat() if row.event_date else None,
                    "title": row.title,
                    "status": row.status,
                    "source_url": row.source_url,
                }
                for row in self.session.execute(
                    action_stmt.order_by(CorporateAction.event_date)
                ).scalars()
            )
        return [event for event in events if event["date"]]


def _bucket_calendar(points: list[ChartPoint], resolution: str) -> list[ChartPoint]:
    """Weekly / monthly buckets for long ranges. Storage is untouched."""
    if not points:
        return []

    def key(day: date) -> date:
        if resolution == "1w":
            return day - timedelta(days=day.weekday())
        return day.replace(day=1)

    grouped: dict[date, list[ChartPoint]] = {}
    for point in points:
        grouped.setdefault(key(point.timestamp.date()), []).append(point)

    out: list[ChartPoint] = []
    for bucket_start in sorted(grouped):
        group = grouped[bucket_start]
        closes = [p.close for p in group if p.close is not None]
        highs = [p.high for p in group if p.high is not None] + closes
        lows = [p.low for p in group if p.low is not None] + closes
        opens = [p.open for p in group if p.open is not None]
        out.append(
            ChartPoint(
                timestamp=datetime.combine(bucket_start, datetime.min.time(), tzinfo=timezone.utc),
                open=opens[0] if opens else (closes[0] if len(closes) > 1 else None),
                high=max(highs) if highs else None,
                low=min(lows) if lows else None,
                close=closes[-1] if closes else None,
                volume=sum(p.volume for p in group if p.volume is not None) or None,
                turnover=sum(p.turnover for p in group if p.turnover is not None) or None,
                trade_count=sum(p.trade_count for p in group if p.trade_count is not None) or None,
                status=STATUS_TRADED if closes else group[-1].status,
            )
        )
    return out


__all__ = ["ChartPoint", "ChartService", "RANGES", "RESOLUTIONS", "resolve_range", "resolve_resolution"]
