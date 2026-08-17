"""Daily series folded out of our own public snapshots, without licensed data."""

from __future__ import annotations

from datetime import datetime, timedelta
import uuid

import pytest

from app.collectors.kase_history_importer import SOURCE as LICENSED_SOURCE
from app.forecast.calendar import KASE_TZ, previous_trading_days
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock, StockQuote
from app.services.series_service import PublicSeriesService

PUBLIC_SOURCE = "kase_public_website"


def _session_at(sessions_ago: int, hour: int) -> datetime:
    """Exchange-time timestamp on the n-th previous KASE trading session.

    Counting sessions rather than calendar days keeps two different arguments
    on two different bars regardless of when the suite runs.
    """
    now = datetime.now(KASE_TZ).replace(hour=hour, minute=0, second=0, microsecond=0)
    return previous_trading_days(now + timedelta(days=1), sessions_ago)[-1]


@pytest.fixture
def public_stock(session):
    """A private share for one test; each test gets its own ticker."""
    suffix = uuid.uuid4().hex[:6].upper()
    issuer = Issuer(code=f"SER{suffix}", name="Series Test JSC", short_name="Series", country="KZ", is_active=True)
    session.add(issuer)
    session.flush()
    instrument = Instrument(
        ticker=f"SERIES{suffix}", isin=None, issuer_id=issuer.id,
        instrument_type="stock", currency="KZT", is_active=True,
    )
    session.add(instrument)
    session.flush()
    stock = Stock(instrument_id=instrument.id, share_class="ordinary")
    session.add(stock)
    session.commit()
    return stock


def _quote(stock_id: int, timestamp: datetime, **values) -> StockQuote:
    return StockQuote(
        stock_id=stock_id, timestamp=timestamp,
        data_mode=values.pop("data_mode", "end_of_day"),
        source=values.pop("source", PUBLIC_SOURCE), **values,
    )


def test_intraday_snapshots_fold_into_one_sampled_bar(session, public_stock):
    day = _session_at(3, 11)
    session.add_all([
        _quote(public_stock.id, day, last=100.0, bid=99.0, ask=101.0, turnover=1_000.0, number_of_trades=2),
        _quote(public_stock.id, day.replace(hour=13), last=104.0, turnover=1_800.0, number_of_trades=5),
        _quote(public_stock.id, day.replace(hour=16), last=102.0, bid=101.5, ask=103.0, turnover=2_500.0, number_of_trades=9),
    ])
    session.commit()

    payload = PublicSeriesService(session).stock(public_stock.instrument.ticker, days=30)
    assert len(payload["sessions"]) == 1
    bar = payload["sessions"][0]
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (100.0, 104.0, 100.0, 102.0)
    assert bar["bar_basis"] == "sampled"
    assert bar["observations"] == 3
    # Running session totals are one number seen three times, not three numbers.
    assert bar["turnover"] == 2_500.0
    assert bar["trades"] == 9
    # The order book is taken from the latest snapshot of the session.
    assert (bar["bid"], bar["ask"]) == (101.5, 103.0)
    assert bar["spread_pct"] == pytest.approx((103.0 - 101.5) / 102.25 * 100)
    assert payload["basis"] == "own_public_snapshots"
    assert payload["price_unit"] == "KZT за акцию"


def test_licensed_rows_are_excluded_by_default_and_counted(session, public_stock):
    public_day, licensed_day = _session_at(2, 15), _session_at(9, 15)
    session.add_all([
        _quote(public_stock.id, public_day, last=120.0),
        _quote(
            public_stock.id, licensed_day, open=110.0, high=115.0, low=108.0, close=112.0,
            last=112.0, source=LICENSED_SOURCE, data_mode="historical",
        ),
    ])
    session.commit()
    service = PublicSeriesService(session)

    public_only = service.stock(public_stock.instrument.ticker, days=60)
    assert public_only["coverage"]["licensed_free"] is True
    assert public_only["coverage"]["licensed_rows_excluded"] == 1
    assert public_only["coverage"]["sources"] == {PUBLIC_SOURCE: 1}
    assert [row["close"] for row in public_only["sessions"]] == [120.0]

    with_licensed = service.stock(public_stock.instrument.ticker, days=60, include_licensed=True)
    assert with_licensed["coverage"]["licensed_free"] is False
    assert with_licensed["coverage"]["native_bars"] == 1
    licensed_bar = with_licensed["sessions"][0]
    # An exchange-published bar keeps its own extremes instead of being sampled.
    assert (licensed_bar["high"], licensed_bar["low"]) == (115.0, 108.0)
    assert licensed_bar["bar_basis"] == "native"


def test_coverage_reports_gaps_and_session_to_session_change(session, public_stock):
    session.add_all([
        _quote(public_stock.id, _session_at(11, 15), last=100.0),
        _quote(public_stock.id, _session_at(1, 15), last=110.0),
    ])
    session.commit()

    payload = PublicSeriesService(session).stock(public_stock.instrument.ticker, days=30)
    coverage = payload["coverage"]
    assert coverage["sessions"] == 2
    assert coverage["expected_sessions"] > 2
    assert coverage["coverage_ratio"] == pytest.approx(2 / coverage["expected_sessions"])
    assert coverage["longest_gap_sessions"] == coverage["expected_sessions"] - 2
    assert payload["sessions"][0]["change_pct"] is None
    assert payload["sessions"][1]["change_pct"] == pytest.approx(10.0)
    assert coverage["chartable"] is True


def test_session_on_a_calendar_holiday_never_pushes_coverage_above_full(session, public_stock):
    from datetime import date, datetime as dt

    from app.forecast.calendar import kase_holidays

    holiday = next(day for day in sorted(kase_holidays(date.today().year)) if day.weekday() < 5)
    session.add_all([
        _quote(public_stock.id, _session_at(3, 15), last=100.0),
        _quote(public_stock.id, _session_at(1, 15), last=101.0),
        _quote(
            public_stock.id,
            dt.combine(holiday, dt.min.time().replace(hour=12), tzinfo=KASE_TZ),
            last=99.0,
        ),
    ])
    session.commit()

    coverage = PublicSeriesService(session).stock(
        public_stock.instrument.ticker, days=400
    )["coverage"]
    assert coverage["sessions_outside_calendar"] == 1
    assert coverage["coverage_ratio"] is not None and coverage["coverage_ratio"] <= 1


def test_single_session_is_honestly_not_chartable(session, public_stock):
    session.add(_quote(public_stock.id, _session_at(1, 15), last=100.0))
    session.commit()

    payload = PublicSeriesService(session).stock(public_stock.instrument.ticker, days=30)
    assert payload["coverage"]["chartable"] is False
    assert "накапливается" in payload["warning"]


def test_zero_prices_are_missing_data_not_a_price(session, public_stock):
    session.add_all([
        _quote(public_stock.id, _session_at(4, 15), last=0.0, close=0.0),
        _quote(public_stock.id, _session_at(3, 15), last=95.0),
        _quote(public_stock.id, _session_at(2, 15), last=97.0),
    ])
    session.commit()

    payload = PublicSeriesService(session).stock(public_stock.instrument.ticker, days=30)
    empty = [row for row in payload["sessions"] if row["close"] is None]
    assert len(empty) == 1
    assert payload["coverage"]["chartable"] is True


def test_bond_series_carries_price_and_yield_from_public_quotes(session, seeded):
    from app.models.bond import Bond
    from app.models.market import BondQuote
    from sqlalchemy import select

    bond = session.execute(select(Bond)).scalars().first()
    assert bond is not None
    session.add_all([
        BondQuote(
            bond_id=bond.id, timestamp=_session_at(3, 12), clean_price=99.5, ytm=0.132,
            bid=99.0, ask=100.0, volume=500.0, turnover=50_000.0, number_of_trades=3,
            data_mode="end_of_day", source=PUBLIC_SOURCE,
        ),
        BondQuote(
            bond_id=bond.id, timestamp=_session_at(2, 12), clean_price=100.4, ytm=0.128,
            volume=700.0, turnover=70_000.0, number_of_trades=4,
            data_mode="end_of_day", source=PUBLIC_SOURCE,
        ),
    ])
    session.commit()

    payload = PublicSeriesService(session).bond(bond.ticker, days=30)
    sessions = {row["date"]: row for row in payload["sessions"]}
    inserted = sessions[_session_at(2, 12).date().isoformat()]
    assert payload["price_unit"] == "% от номинала"
    assert inserted["close"] == 100.4
    assert inserted["ytm"] == pytest.approx(0.128)
    assert payload["coverage"]["licensed_free"] is True


def test_series_endpoints_are_public_and_licence_free(api, seeded):
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.bond import Bond

    with SessionLocal() as db:
        ticker = db.execute(select(Bond.ticker)).scalars().first()

    response = api.get(f"/bonds/{ticker}/series?days=365")
    assert response.status_code == 200
    payload = response.json()
    assert payload["basis"] == "own_public_snapshots"
    assert payload["coverage"]["licensed_free"] is True
    assert LICENSED_SOURCE not in payload["coverage"]["sources"]
    assert isinstance(payload["markers"], list)

    assert api.get(f"/bonds/{ticker}/series?days=0").status_code == 422
