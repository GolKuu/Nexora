"""Every screen tells the same story about the same stock.

Prices used to arrive on two tracks: ``stock_quotes`` fed the card, the metrics
and the forecast, while ``market_observations`` fed the history chart. Both were
real and they disagreed. These tests pin the rule that resolved it - the
permanent history is canonical, quotes are staging, and any endpoint that shows
a price shows *that* price.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.forecast.calendar import KASE_TZ
from app.models.history import MarketObservation
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock, StockQuote
from app.services.backfill.records import ObservationRecord, STATUS_TRADED
from app.services.backfill.store import HistoryStore
from app.services.monitoring import MonitoringService
from app.services.price_service import PriceService
from app.services.stock_service import StockService


def _moment(day: date, hour: int = 17) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=KASE_TZ).replace(
        hour=hour
    ).astimezone(timezone.utc)


@pytest.fixture
def synced_stock(session):
    """A stock carrying a fortnight of stored daily history."""
    unique = uuid.uuid4().hex[:8].upper()
    issuer = Issuer(name="Sync Issuer", code=f"SYNCISS{unique}", sector="corporate")
    session.add(issuer)
    session.flush()
    instrument = Instrument(
        ticker=f"SYNC{unique}", isin=f"KZ{unique}SYNC", issuer_id=issuer.id,
        instrument_type="stock", currency="KZT", is_active=True,
        kase_url=f"https://kase.kz/en/investors/shares/SYNC{unique}/",
    )
    session.add(instrument)
    session.flush()
    session.add(Stock(instrument_id=instrument.id, lot_size=1, liquidity_class=1))
    session.flush()

    days = [date(2026, 8, 3) + timedelta(days=offset) for offset in range(10)]
    days = [day for day in days if day.weekday() < 5]
    store = HistoryStore(session)
    store.save_observations(instrument.id, [
        ObservationRecord(
            observed_at=_moment(day),
            price=1000.0 + index,
            bid=999.0 + index,
            ask=1001.0 + index,
            volume=500.0,
            turnover=(1000.0 + index) * 500.0,
            trade_count=10,
            status=STATUS_TRADED,
            source="kase_public_website",
            source_url=instrument.kase_url,
            data_mode="browser",
            trading_date=day,
        )
        for index, day in enumerate(days)
    ])
    store.rebuild_daily_snapshots(instrument.id)
    session.commit()
    return instrument


def _chart_last_close(payload: dict) -> float | None:
    traded = [point for point in payload["series"] if point.get("close") is not None]
    return traded[-1]["close"] if traded else None


def test_card_chart_history_and_series_agree_on_the_price(client, synced_stock):
    ticker = synced_stock.ticker
    detail = client.get(f"/api/v1/stocks/{ticker}").json()
    chart = client.get(f"/api/v1/stocks/{ticker}/chart?range=1m").json()
    history = client.get(f"/api/v1/stocks/{ticker}/history").json()
    series = client.get(f"/api/v1/stocks/{ticker}/series?days=60").json()

    price = detail["price"]
    assert price is not None
    assert _chart_last_close(chart) == price, "the card and the chart end on one number"
    assert history["quotes"][-1]["close"] == price
    assert series["sessions"][-1]["close"] == price
    assert detail["price_origin"] == "history"
    assert history["price_origin"] == "history"


def test_a_correction_moves_every_screen_at_once(client, session, synced_stock):
    """KASE restates a price: the card, the chart and the history all follow."""
    ticker = synced_stock.ticker
    before = client.get(f"/api/v1/stocks/{ticker}").json()["price"]
    last_day = session.query(MarketObservation).filter_by(
        instrument_id=synced_stock.id
    ).order_by(MarketObservation.observed_at.desc()).first().trading_date

    store = HistoryStore(session)
    outcome = store.save_observations(synced_stock.id, [
        ObservationRecord(
            observed_at=_moment(last_day),
            price=before + 250.0,
            status=STATUS_TRADED,
            source="kase_public_website",
            source_url=synced_stock.kase_url,
            data_mode="browser",
            trading_date=last_day,
        )
    ])
    store.rebuild_daily_snapshots(synced_stock.id)
    session.commit()

    assert outcome["corrections"] == 1
    detail = client.get(f"/api/v1/stocks/{ticker}").json()
    chart = client.get(f"/api/v1/stocks/{ticker}/chart?range=1m").json()
    history = client.get(f"/api/v1/stocks/{ticker}/history").json()

    assert detail["price"] == before + 250.0
    assert _chart_last_close(chart) == detail["price"]
    assert history["quotes"][-1]["close"] == detail["price"]


def test_a_new_quote_reaches_the_chart_through_promotion(session, synced_stock):
    """A raw quote is staging: promotion is what makes it visible everywhere."""
    stock = session.query(Stock).filter_by(instrument_id=synced_stock.id).one()
    later = _moment(date(2026, 8, 17), hour=12)
    session.add(StockQuote(
        stock_id=stock.id, timestamp=later, last=1234.0, close=1234.0,
        bid=1233.0, ask=1235.0, volume=10.0, data_mode="delayed",
        source="kase_public_website",
        source_url=synced_stock.kase_url,
        source_timestamp=later,
    ))
    session.flush()

    prices = PriceService(session)
    assert prices.latest(synced_stock.id, stock_id=stock.id).price != 1234.0, (
        "an unpromoted quote must not quietly overtake the canonical record"
    )

    MonitoringService(session).promote_quotes(synced_stock, stock)
    session.flush()

    canonical = prices.latest(synced_stock.id, stock_id=stock.id)
    assert canonical.price == 1234.0
    assert canonical.origin == "history"
    assert prices.daily_series(synced_stock.id, stock_id=stock.id)[0][-1]["close"] == 1234.0


def test_promotion_is_idempotent(session, synced_stock):
    stock = session.query(Stock).filter_by(instrument_id=synced_stock.id).one()
    moment = _moment(date(2026, 8, 18), hour=12)
    session.add(StockQuote(
        stock_id=stock.id, timestamp=moment, last=1111.0, close=1111.0,
        data_mode="delayed", source="kase_public_website", source_timestamp=moment,
    ))
    session.flush()

    monitoring = MonitoringService(session)
    first = monitoring.promote_quotes(synced_stock, stock)
    second = monitoring.promote_quotes(synced_stock, stock)

    assert first["created"] == 1
    assert second["created"] == 0
    assert session.query(MarketObservation).filter_by(
        instrument_id=synced_stock.id
    ).filter(MarketObservation.price == 1111.0).count() == 1


def test_metrics_are_computed_from_the_charted_closes(session, synced_stock):
    """Volatility and drawdown must be traceable to points the user can see."""
    stock = session.query(Stock).filter_by(instrument_id=synced_stock.id).one()
    metrics, _, _ = StockService(session)._inputs(stock)
    closes = PriceService(session).daily_closes(synced_stock.id, limit=252)

    assert metrics["price"] == closes[-1]
    assert metrics["volatility"] is not None
    assert len(closes) >= 5


def test_a_stock_without_history_says_so_instead_of_pretending(session):
    """No history yet: the quote is used, and labelled as such."""
    unique = uuid.uuid4().hex[:8].upper()
    issuer = Issuer(name="Fresh Issuer", code=f"FRESH{unique}", sector="corporate")
    session.add(issuer)
    session.flush()
    instrument = Instrument(
        ticker=f"FRESH{unique}", isin=f"KZ{unique}FRSH", issuer_id=issuer.id,
        instrument_type="stock", currency="KZT", is_active=True,
    )
    session.add(instrument)
    session.flush()
    stock = Stock(instrument_id=instrument.id, lot_size=1)
    session.add(stock)
    session.flush()
    moment = _moment(date(2026, 8, 14))
    session.add(StockQuote(
        stock_id=stock.id, timestamp=moment, last=500.0, close=500.0,
        data_mode="delayed", source="kase_public_website", source_timestamp=moment,
    ))
    session.flush()

    canonical = PriceService(session).latest(instrument.id, stock_id=stock.id)
    assert canonical.price == 500.0
    assert canonical.origin == "quote", "the fallback is visible, not disguised as history"

    rows, origin = PriceService(session).daily_series(instrument.id, stock_id=stock.id)
    assert origin == "quote"
    assert rows[-1]["close"] == 500.0
    assert rows[-1]["open"] is None, "one reading is a close, never a candle"
