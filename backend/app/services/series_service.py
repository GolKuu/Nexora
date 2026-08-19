"""Daily chart series built from our own publicly collected snapshots.

KASE sells its trading archive as a commercial product, so a chart that needs
the licensed deals register cannot be part of the public product. Everything
here is assembled from data the application already stores from the public
feeds: session snapshots in ``stock_quotes`` / ``bond_quotes``, the aggregated
session deal in ``bond_trades``, and the change log in ``data_change_sets``.

Two honesty rules shape the payload:

* **A bar says how it was made.** When the source row already carries an
  exchange-published OHLC (the licensed importer writes one), the bar is
  ``native``. When the bar is folded out of several intraday snapshots that we
  polled ourselves, it is ``sampled`` - the high and low are then the extremes
  *we observed*, not the session extremes, and ``observations`` says how many
  points that judgement rests on.
* **Volume is not summed.** KASE publishes ``vol``/``volkzt``/``dealcnt`` as
  running session totals, so two snapshots of the same session are two views of
  one number. The session value is therefore the maximum, never the sum.

Licensed rows are excluded by default and counted, so a chart drawn without
them can be shown to be free of licensed data rather than merely claimed to be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Literal, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.kase_history_importer import SOURCE as LICENSED_SOURCE
from app.core.enums import DataMode
from app.forecast.calendar import KASE_TZ, kase_date, kase_holidays
from app.models.history import DailyMarketSnapshot
from app.models.incremental import DataChangeSet
from app.models.market import BondQuote, BondTrade
from app.models.stock import StockQuote
from app.services.backfill.records import STATUS_TRADED
from app.services.bond_service import BondService
from app.services.price_service import PriceService
from app.services.stock_service import StockService

#: Series basis reported to the client. Kept as a literal string so a chart can
#: label the axis without inferring provenance from the numbers.
BASIS = "own_public_snapshots"

#: Sources whose rows come from the operator's licensed KASE archive purchase.
LICENSED_SOURCES = {LICENSED_SOURCE}

MAX_DAYS = 1825


def _is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in kase_holidays(day.year)


def _expected_sessions(first: date, last: date) -> int:
    count, cursor = 0, first
    while cursor <= last:
        if _is_trading_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def _aware(value: datetime) -> datetime:
    """Make a stored timestamp comparable.

    Postgres returns these tz-aware, but a SQLite deployment can hand back a
    naive value for a row written before the column carried an offset. A naive
    reading is exchange-local (that is what ``kase_date`` assumes too), so it is
    stamped with the exchange offset rather than with UTC - stamping UTC would
    move late-session snapshots onto the wrong trading day.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=KASE_TZ)


def _positive(value: float | None) -> float | None:
    """Prices are strictly positive; a zero here means "not published"."""
    return value if value is not None and value > 0 else None


def _non_negative(value: float | None) -> float | None:
    return value if value is not None and value >= 0 else None


@dataclass
class _Bar:
    """One trading session folded out of one or more stored snapshots."""

    day: date
    first_at: datetime
    last_at: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    bid: float | None = None
    ask: float | None = None
    ytm: float | None = None
    ytm_high: float | None = None
    ytm_low: float | None = None
    volume: float | None = None
    turnover: float | None = None
    trades: int | None = None
    observations: int = 0
    native: bool = False
    sources: set[str] = field(default_factory=set)
    data_modes: set[str] = field(default_factory=set)

    def observe_price(self, timestamp: datetime, price: float | None) -> None:
        if price is None:
            return
        if self.open is None or timestamp <= self.first_at:
            self.open = price
        if self.close is None or timestamp >= self.last_at:
            self.close = price
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)

    def observe_native_bar(
        self, open_: float | None, high: float | None, low: float | None, close: float | None
    ) -> None:
        """Adopt an exchange-published bar; it outranks anything we sampled."""
        self.native = True
        self.open, self.close = open_, close
        self.high = high if high is not None else max(v for v in (open_, close) if v is not None)
        self.low = low if low is not None else min(v for v in (open_, close) if v is not None)

    def observe_ytm(self, timestamp: datetime, ytm: float | None) -> None:
        if ytm is None:
            return
        if self.ytm is None or timestamp >= self.last_at:
            self.ytm = ytm
        self.ytm_high = ytm if self.ytm_high is None else max(self.ytm_high, ytm)
        self.ytm_low = ytm if self.ytm_low is None else min(self.ytm_low, ytm)

    def observe_cumulative(
        self, volume: float | None, turnover: float | None, trades: int | None
    ) -> None:
        """Session running totals: keep the largest view, never the sum."""
        if volume is not None:
            self.volume = volume if self.volume is None else max(self.volume, volume)
        if turnover is not None:
            self.turnover = turnover if self.turnover is None else max(self.turnover, turnover)
        if trades is not None:
            self.trades = trades if self.trades is None else max(self.trades, trades)

    def observe_book(self, timestamp: datetime, bid: float | None, ask: float | None) -> None:
        if timestamp >= self.last_at or self.bid is None:
            if bid is not None:
                self.bid = bid
        if timestamp >= self.last_at or self.ask is None:
            if ask is not None:
                self.ask = ask

    def touch(self, timestamp: datetime, source: str | None, data_mode: str | None) -> None:
        self.first_at = min(self.first_at, timestamp)
        self.last_at = max(self.last_at, timestamp)
        self.observations += 1
        if source:
            self.sources.add(source)
        if data_mode:
            self.data_modes.add(data_mode)

    def serialize(self, previous_close: float | None) -> dict:
        spread_pct = None
        if self.bid and self.ask and self.ask >= self.bid:
            mid = (self.bid + self.ask) / 2
            spread_pct = (self.ask - self.bid) / mid * 100 if mid else None
        change_pct = None
        if previous_close and self.close is not None:
            change_pct = (self.close / previous_close - 1) * 100
        return {
            "date": self.day.isoformat(),
            "timestamp": self.last_at.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "bid": self.bid,
            "ask": self.ask,
            "spread_pct": spread_pct,
            "ytm": self.ytm,
            "ytm_high": self.ytm_high,
            "ytm_low": self.ytm_low,
            "volume": self.volume,
            "turnover": self.turnover,
            "trades": self.trades,
            "change_pct": change_pct,
            "observations": self.observations,
            "bar_basis": "native" if self.native else "sampled",
            "sources": sorted(self.sources),
            "data_mode": sorted(self.data_modes)[0] if self.data_modes else None,
        }


class PublicSeriesService:
    """Chart-ready series and change markers, licence-free by default."""

    def __init__(self, session: Session):
        self.session = session

    # -- public API -------------------------------------------------------

    def stock(
        self, identifier: str, *, days: int = 365, include_licensed: bool = False
    ) -> dict:
        stock = StockService(self.session).require(identifier)
        cutoff = self._cutoff(days)
        # The permanent history is the canonical series: it holds everything the
        # backfill recovered from kase.kz as well as everything we sampled
        # ourselves, and it reflects published corrections. Folding quotes again
        # here would draw a second, slightly different chart from the same facts.
        snapshots = PriceService(self.session).daily_snapshots(
            stock.instrument_id, start=kase_date(cutoff), limit=None, traded_only=False
        )
        if snapshots:
            bars, excluded = self._fold_daily_snapshots(
                snapshots, include_licensed=include_licensed
            )
        else:
            rows = self.session.execute(
                select(StockQuote)
                .where(StockQuote.stock_id == stock.id, StockQuote.timestamp >= cutoff)
                .order_by(StockQuote.timestamp, StockQuote.id)
            ).scalars()
            bars, excluded = self._fold_stock_quotes(rows, include_licensed=include_licensed)
        return self._payload(
            ticker=stock.instrument.ticker,
            instrument_type="stock",
            entity_id=str(stock.id),
            bars=bars,
            days=days,
            excluded=excluded,
            include_licensed=include_licensed,
            price_unit=f"{stock.instrument.currency or 'KZT'} за акцию",
            extra={
                "isin": stock.instrument.isin,
                "name": stock.instrument.issuer.short_name or stock.instrument.issuer.name,
                "currency": stock.instrument.currency or "KZT",
                "kase_url": stock.instrument.kase_url,
            },
        )

    def bond(
        self, identifier: str, *, days: int = 365, include_licensed: bool = False
    ) -> dict:
        bond = BondService(self.session).require(identifier)
        cutoff = self._cutoff(days)
        quotes = self.session.execute(
            select(BondQuote)
            .where(BondQuote.bond_id == bond.id, BondQuote.timestamp >= cutoff)
            .order_by(BondQuote.timestamp, BondQuote.id)
        ).scalars()
        bars, excluded = self._fold_bond_quotes(quotes, include_licensed=include_licensed)
        trades = self.session.execute(
            select(BondTrade)
            .where(BondTrade.bond_id == bond.id, BondTrade.timestamp >= cutoff)
            .order_by(BondTrade.timestamp, BondTrade.id)
        ).scalars()
        excluded += self._merge_bond_trades(bars, trades, include_licensed=include_licensed)
        return self._payload(
            ticker=bond.ticker,
            instrument_type="bond",
            entity_id=str(bond.id),
            bars=bars,
            days=days,
            excluded=excluded,
            include_licensed=include_licensed,
            price_unit="% от номинала",
            extra={
                "isin": bond.isin,
                "name": bond.name,
                "currency": bond.currency,
                "kase_url": bond.kase_url,
                "maturity_date": bond.maturity_date.isoformat() if bond.maturity_date else None,
            },
        )

    # -- folding ----------------------------------------------------------

    def _fold_daily_snapshots(
        self, rows: Iterable[DailyMarketSnapshot], *, include_licensed: bool
    ) -> tuple[dict[date, _Bar], int]:
        """One bar per stored trading day, straight from the canonical history.

        The bar is ``native`` only when the day carries a real open and close;
        a day the source published as a single price stays a close with no
        manufactured high or low, exactly as the chart draws it.
        """
        bars: dict[date, _Bar] = {}
        excluded = 0
        for row in rows:
            if not include_licensed and (row.source or "") in LICENSED_SOURCES:
                excluded += 1
                continue
            if row.status != STATUS_TRADED and row.close is None:
                continue
            moment = _aware(row.last_observation_at or row.first_observation_at) if (
                row.last_observation_at or row.first_observation_at
            ) else datetime.combine(row.trading_date, datetime.min.time(), tzinfo=KASE_TZ)
            bar = _Bar(day=row.trading_date, first_at=moment, last_at=moment)
            bar.touch(moment, row.source, row.data_mode)
            bar.observations = max(row.observation_count, 1)
            if row.open is not None and row.close is not None:
                bar.observe_native_bar(row.open, row.high, row.low, row.close)
            else:
                bar.close = row.close
                bar.high, bar.low = row.high, row.low
            bar.bid, bar.ask = row.bid_close, row.ask_close
            bar.observe_cumulative(row.volume, row.turnover, row.trade_count)
            bars[row.trading_date] = bar
        return bars, excluded

    def _fold_stock_quotes(
        self, rows: Iterable[StockQuote], *, include_licensed: bool
    ) -> tuple[dict[date, _Bar], int]:
        bars: dict[date, _Bar] = {}
        excluded = 0
        for row in rows:
            if not include_licensed and (row.source or "") in LICENSED_SOURCES:
                excluded += 1
                continue
            timestamp = _aware(row.timestamp)
            bar = self._bar_for(bars, timestamp)
            bar.touch(timestamp, row.source, row.data_mode)
            native_open, native_high = _positive(row.open), _positive(row.high)
            native_low, native_close = _positive(row.low), _positive(row.close)
            if native_open is not None and native_close is not None:
                bar.observe_native_bar(native_open, native_high, native_low, native_close)
            else:
                # The public catalogue publishes only a last price and the
                # previous session's close, so the bar is what we sampled.
                bar.observe_price(timestamp, _positive(row.last) or native_close)
            bar.observe_book(timestamp, _positive(row.bid), _positive(row.ask))
            bar.observe_cumulative(
                _non_negative(row.volume), _non_negative(row.turnover), row.number_of_trades
            )
        return bars, excluded

    def _fold_bond_quotes(
        self, rows: Iterable[BondQuote], *, include_licensed: bool
    ) -> tuple[dict[date, _Bar], int]:
        bars: dict[date, _Bar] = {}
        excluded = 0
        for row in rows:
            if not include_licensed and (row.source or "") in LICENSED_SOURCES:
                excluded += 1
                continue
            timestamp = _aware(row.timestamp)
            bar = self._bar_for(bars, timestamp)
            bar.touch(timestamp, row.source, row.data_mode)
            bar.observe_price(timestamp, _positive(row.clean_price) or _positive(row.last))
            bar.observe_ytm(timestamp, row.ytm)
            bar.observe_book(timestamp, _positive(row.bid), _positive(row.ask))
            bar.observe_cumulative(
                _non_negative(row.volume), _non_negative(row.turnover), row.number_of_trades
            )
        return bars, excluded

    def _merge_bond_trades(
        self, bars: dict[date, _Bar], rows: Iterable[BondTrade], *, include_licensed: bool
    ) -> int:
        """Add sessions known only from the aggregated public deal record.

        KASE publishes one volume-weighted record per session rather than a
        tick log, so a traded session can exist in ``bond_trades`` while the
        quote feed carried nothing usable.
        """
        excluded = 0
        for row in rows:
            if not include_licensed and (row.source or "") in LICENSED_SOURCES:
                excluded += 1
                continue
            timestamp = _aware(row.timestamp)
            known = kase_date(timestamp) in bars
            bar = self._bar_for(bars, timestamp)
            if not known:
                bar.touch(timestamp, row.source, row.data_mode)
                bar.observe_price(timestamp, _positive(row.clean_price) or _positive(row.price))
                bar.observe_ytm(timestamp, row.ytm)
            bar.observe_cumulative(
                _non_negative(row.quantity), _non_negative(row.amount), None
            )
        return excluded

    @staticmethod
    def _bar_for(bars: dict[date, _Bar], timestamp: datetime) -> _Bar:
        day = kase_date(timestamp)
        bar = bars.get(day)
        if bar is None:
            bar = _Bar(day=day, first_at=timestamp, last_at=timestamp)
            bars[day] = bar
        return bar

    # -- assembly ---------------------------------------------------------

    def _payload(
        self,
        *,
        ticker: str,
        instrument_type: Literal["stock", "bond"],
        entity_id: str,
        bars: dict[date, _Bar],
        days: int,
        excluded: int,
        include_licensed: bool,
        price_unit: str,
        extra: dict,
    ) -> dict:
        ordered = [bars[day] for day in sorted(bars)]
        sessions: list[dict] = []
        previous_close: float | None = None
        for bar in ordered:
            sessions.append(bar.serialize(previous_close))
            if bar.close is not None:
                previous_close = bar.close
        markers = self._markers(entity_id, instrument_type, [bar.day for bar in ordered])
        marker_counts = {row["date"]: row["count"] for row in markers}
        for row in sessions:
            row["change_events"] = marker_counts.get(row["date"], 0)
        coverage = self._coverage(ordered, days=days, excluded=excluded, include_licensed=include_licensed)
        return {
            "ticker": ticker,
            "instrument_type": instrument_type,
            "basis": BASIS,
            "price_unit": price_unit,
            "sessions": sessions,
            "markers": markers,
            "coverage": coverage,
            "warning": self._warning(coverage),
            **extra,
        }

    def _coverage(
        self, bars: Sequence[_Bar], *, days: int, excluded: int, include_licensed: bool
    ) -> dict:
        sources: dict[str, int] = {}
        modes: dict[str, int] = {}
        for bar in bars:
            for source in bar.sources:
                sources[source] = sources.get(source, 0) + 1
            for mode in bar.data_modes:
                modes[mode] = modes.get(mode, 0) + 1
        first = bars[0].day if bars else None
        last = bars[-1].day if bars else None
        expected = _expected_sessions(first, last) if first and last else 0
        # A session can land on a day our holiday table calls closed - the
        # exchange traded, or the table drifted. Counting those against the
        # expected total would push coverage above 100%, so they are reported
        # separately instead of inflating the ratio.
        on_calendar = sum(1 for bar in bars if _is_trading_day(bar.day))
        longest_gap = 0
        for previous, current in zip(bars, bars[1:]):
            gap = _expected_sessions(previous.day + timedelta(days=1), current.day) - 1
            longest_gap = max(longest_gap, max(0, gap))
        return {
            "requested_days": days,
            "sessions": len(bars),
            "observations": sum(bar.observations for bar in bars),
            "first_session": first.isoformat() if first else None,
            "last_session": last.isoformat() if last else None,
            "expected_sessions": expected,
            "sessions_outside_calendar": len(bars) - on_calendar,
            "coverage_ratio": (on_calendar / expected) if expected else None,
            "longest_gap_sessions": longest_gap,
            "native_bars": sum(1 for bar in bars if bar.native),
            "sampled_bars": sum(1 for bar in bars if not bar.native),
            "sources": sources,
            "data_modes": modes,
            "includes_licensed": include_licensed,
            "licensed_rows_excluded": excluded,
            "licensed_free": not include_licensed,
            "mock": DataMode.MOCK.value in modes,
            "chartable": sum(1 for bar in bars if bar.close is not None) >= 2,
        }

    def _markers(
        self, entity_id: str, entity_type: str, session_days: Sequence[date]
    ) -> list[dict]:
        """Material changes grouped onto the session they were detected in.

        Only sessions that exist in the series get a marker; a change detected
        on a day we have no price for has nothing to attach to on the chart and
        stays visible in the change feed instead.
        """
        if not session_days:
            return []
        # Exchange-local midnight: the stored values are session timestamps, so
        # the window has to be expressed on the same clock the sessions use.
        window_start = datetime.combine(
            session_days[0], datetime.min.time(), tzinfo=KASE_TZ
        )
        rows = list(
            self.session.execute(
                select(DataChangeSet)
                .where(
                    DataChangeSet.entity_type == entity_type,
                    DataChangeSet.entity_id == entity_id,
                    DataChangeSet.material.is_(True),
                    DataChangeSet.detected_at >= window_start,
                )
                .order_by(DataChangeSet.detected_at)
            ).scalars()
        )
        known = set(session_days)
        grouped: dict[date, list[DataChangeSet]] = {}
        for row in rows:
            day = kase_date(_aware(row.detected_at))
            if day in known:
                grouped.setdefault(day, []).append(row)
        markers = []
        for day in sorted(grouped):
            events = sorted(grouped[day], key=lambda row: row.importance, reverse=True)
            markers.append(
                {
                    "date": day.isoformat(),
                    "count": len(events),
                    "max_importance": events[0].importance,
                    "sections": sorted({row.section for row in events}),
                    "top": [
                        {
                            "section": row.section,
                            "field": row.field,
                            "old_value": row.old_value,
                            "new_value": row.new_value,
                            "change_type": row.change_type,
                            "importance": row.importance,
                            "source_url": row.source_url,
                        }
                        for row in events[:3]
                    ],
                }
            )
        return markers

    @staticmethod
    def _warning(coverage: dict) -> str | None:
        if coverage["mock"]:
            return "Показаны демонстрационные данные: KASE не подключен, цифры синтетические."
        if not coverage["chartable"]:
            return (
                "История ещё накапливается: график появится после нескольких "
                "сессий публичных данных KASE."
            )
        if coverage["sampled_bars"] and not coverage["native_bars"]:
            return (
                "Максимум и минимум сессии — это наблюдённые нами значения "
                "публичного фида, а не официальные экстремумы сессии."
            )
        return None

    @staticmethod
    def _cutoff(days: int) -> datetime:
        return datetime.now(timezone.utc) - timedelta(days=min(max(days, 1), MAX_DAYS))


__all__ = ["BASIS", "LICENSED_SOURCES", "MAX_DAYS", "PublicSeriesService"]
