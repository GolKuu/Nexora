"""The historical backfill, tested without touching kase.kz.

The collector is injectable, so the whole pipeline - discovery, window, parse,
validation, storage, daily aggregation, coverage, resume, charts - runs against
fixtures. The one thing these tests cannot prove is that KASE's HTML still looks
the way we think it does; that is what the live test in
``tests/test_live_backfill.py`` is for.

The rules under test are the ones the product depends on: never fabricate a
price, never duplicate a re-run, never claim coverage we do not have.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from app.forecast.calendar import KASE_TZ
from app.models.history import (
    BackfillCheckpoint,
    DailyMarketSnapshot,
    HistoricalTrade,
    IngestionAnomaly,
    MarketObservation,
)
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock
from app.services.backfill.chart_api import parse_history
from app.services.backfill.collector import PageHistory
from app.services.backfill.coverage import CoverageService
from app.services.backfill.parser import (
    map_headers,
    parse_dividends,
    parse_price_history,
    parse_publication_links,
    parse_report_documents,
    parse_trades,
)
from app.services.backfill.queue import (
    LEASE,
    BackfillQueue,
    PRIORITY_PORTFOLIO,
    PRIORITY_UNIVERSE,
    PRIORITY_WATCHLIST,
    STATUS_COMPLETED,
    STATUS_PARTIAL,
    STATUS_PROCESSING,
)
from app.services.backfill.records import (
    CollectionResult,
    DividendRecord,
    ObservationRecord,
    ReportRecord,
    STATUS_NO_TRADE,
    STATUS_TRADED,
    TradeRecord,
)
from app.services.backfill.runner import BackfillRunner
from app.services.backfill.store import HistoryStore
from app.services.backfill.validate import validate_observations, validate_trades
from app.services.backfill.window import (
    backfill_window,
    expected_market_days,
    is_market_day,
    market_days,
    shift_years,
)
from app.services.chart_service import ChartService

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def instrument(session) -> Instrument:
    unique = uuid.uuid4().hex[:8].upper()
    issuer = Issuer(name="Test Issuer", code=f"BFISS{unique}", sector="corporate")
    session.add(issuer)
    session.flush()
    row = Instrument(
        ticker=f"BF{unique}",
        isin=f"KZ{unique}TST",
        issuer_id=issuer.id,
        instrument_type="stock",
        currency="KZT",
        kase_url=f"https://kase.kz/en/investors/bonds/BF{unique}",
        is_active=True,
    )
    session.add(row)
    session.flush()
    # No liquidity class: the plain "rest of the universe" case.
    session.add(Stock(instrument_id=row.id))
    session.flush()
    return row


def observation(day: date, price: float | None, **kwargs) -> ObservationRecord:
    moment = datetime.combine(day, datetime.min.time(), tzinfo=KASE_TZ).replace(hour=17)
    return ObservationRecord(
        observed_at=moment.astimezone(timezone.utc),
        price=price,
        status=STATUS_TRADED if price is not None else STATUS_NO_TRADE,
        source="kase_public_website",
        source_url="https://kase.kz/en/investors/bonds/BFTST",
        data_mode="browser",
        trading_date=day,
        **kwargs,
    )


class FakeCollector:
    """Stands in for the browser. Records what it was asked for."""

    def __init__(
        self,
        result: CollectionResult,
        *,
        error: Exception | None = None,
        endpoints: list[dict] | None = None,
    ):
        self.result = result
        self.error = error
        self.endpoints = endpoints or []
        self.calls: list[dict] = []

    async def collect(self, ticker, window, *, url=None, since=None) -> PageHistory:
        self.calls.append({"ticker": ticker, "url": url, "since": since, "window": window})
        if self.error is not None:
            raise self.error
        return PageHistory(
            result=self.result, status="ok", observed_endpoints=list(self.endpoints)
        )


def collection(observations=(), trades=(), dividends=(), reports=(), **kwargs) -> CollectionResult:
    return CollectionResult(
        observations=list(observations), trades=list(trades),
        dividends=list(dividends), reports=list(reports),
        source_urls=["https://kase.kz/en/investors/bonds/BFTST"], pages_visited=1,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# the window (§9, §24)
# ---------------------------------------------------------------------------


def test_window_is_relative_to_the_execution_date():
    window = backfill_window(now=NOW, years=2)
    assert window.start_date == date(2024, 8, 17)
    assert window.end_date == date(2026, 8, 17)
    assert window.years == 2

    later = backfill_window(now=NOW + timedelta(days=365), years=2)
    assert later.start_date == date(2025, 8, 17), "the window rolls with the clock"


def test_window_survives_a_leap_day():
    leap = datetime(2028, 2, 29, 12, 0, tzinfo=timezone.utc)
    window = backfill_window(now=leap, years=2)
    # 29 February 2026 does not exist; the last real day of that month is used.
    assert window.start_date == date(2026, 2, 28)
    assert shift_years(date(2024, 2, 29), 4) == date(2020, 2, 29)


def test_market_days_exclude_weekends_and_holidays():
    days = market_days(date(2026, 1, 1), date(2026, 1, 31))
    assert date(2026, 1, 1) not in days, "New Year is a holiday"
    assert all(day.weekday() < 5 for day in days)
    assert expected_market_days(date(2026, 1, 1), date(2026, 1, 31)) == len(days)
    assert is_market_day(date(2026, 1, 5)) is True


def test_two_year_window_has_roughly_two_years_of_sessions():
    window = backfill_window(now=NOW, years=2)
    expected = expected_market_days(window.start_date, window.end_date)
    assert 470 <= expected <= 530, expected


# ---------------------------------------------------------------------------
# parsing (§4, §6, §12)
# ---------------------------------------------------------------------------


def test_headers_are_discovered_in_russian_and_english():
    mapping = map_headers(["Дата", "Цена закрытия", "Объем, шт", "Оборот", "Сделок"])
    assert mapping["date"] == 0
    assert mapping["close"] == 1
    assert mapping["volume"] == 2
    assert mapping["turnover"] == 3
    assert mapping["trade_count"] == 4

    english = map_headers(["Date", "Open", "High", "Low", "Close", "Volume"])
    assert set(english) == {"date", "open", "high", "low", "close", "volume"}


def test_single_price_column_never_becomes_ohlc():
    records = parse_price_history(
        ["Дата", "Цена"], [["17.08.2026", "1 250,50"]]
    )
    assert len(records) == 1
    record = records[0]
    assert record.price == pytest.approx(1250.50)
    assert record.open is None and record.high is None and record.low is None


def test_a_day_with_no_numbers_is_stored_as_no_trade():
    records = parse_price_history(["Дата", "Цена", "Объем"], [["17.08.2026", "-", "-"]])
    assert records[0].status == STATUS_NO_TRADE
    assert records[0].price is None, "a quiet day is not yesterday's price"


def test_trades_and_dividends_are_parsed():
    trades = parse_trades(
        ["Дата", "Время", "Цена", "Количество", "Номер сделки"],
        [["17.08.2026", "11:32:10", "1 250,50", "100", "T-1"]],
    )
    assert trades[0].price == pytest.approx(1250.50)
    assert trades[0].quantity == pytest.approx(100)
    assert trades[0].trade_id == "T-1"
    assert trades[0].trade_timestamp.tzinfo is not None

    dividends = parse_dividends(
        ["Дата выплаты", "Размер дивиденда на одну акцию", "Валюта"],
        [["01.07.2026", "120,00", "KZT"]],
    )
    assert dividends[0].amount_per_share == pytest.approx(120.0)
    assert dividends[0].payment_date == date(2026, 7, 1)


# ---------------------------------------------------------------------------
# parser safety (§29)
# ---------------------------------------------------------------------------


def test_impossible_values_are_rejected_not_stored():
    good = observation(date(2026, 8, 14), 1000.0)
    outcome = validate_observations(
        [good, observation(date(2026, 8, 13), -5.0)], now=NOW
    )
    assert outcome.accepted == [good]
    assert outcome.rejections[0].kind == "impossible_price"


def test_broken_timestamps_are_rejected():
    broken = ObservationRecord(
        observed_at=datetime(1970, 1, 1, tzinfo=timezone.utc), price=100.0
    )
    outcome = validate_observations([broken], now=NOW)
    assert not outcome.accepted
    assert outcome.rejections[0].kind == "broken_timestamp"


def test_a_mostly_broken_batch_is_rejected_wholesale():
    records = [observation(date(2026, 8, 10), 1000.0)] + [
        ObservationRecord(observed_at=NOW, price=-1.0) for _ in range(4)
    ]
    outcome = validate_observations(records, now=NOW)
    assert outcome.batch_rejected is True
    assert outcome.accepted == [], "a broken parser must not write partial history"


def test_an_impossible_move_against_stored_history_is_rejected():
    outcome = validate_observations(
        [observation(date(2026, 8, 14), 50_000.0)], reference_price=1000.0, now=NOW
    )
    assert not outcome.accepted
    assert outcome.rejections[0].kind == "unrealistic_move"


def test_trade_validation_rejects_nonpositive_quantities():
    outcome = validate_trades(
        [TradeRecord(trade_timestamp=NOW - timedelta(days=1), price=100.0, quantity=0.0)],
        now=NOW,
    )
    assert not outcome.accepted


# ---------------------------------------------------------------------------
# storage and idempotency (§18)
# ---------------------------------------------------------------------------


def test_repeated_storage_creates_no_duplicates(session, instrument):
    store = HistoryStore(session)
    records = [observation(date(2026, 8, 10 + i), 1000.0 + i) for i in range(3)]

    first = store.save_observations(instrument.id, records)
    second = store.save_observations(instrument.id, records)

    assert first["created"] == 3
    assert second["created"] == 0
    assert second["duplicates"] == 3
    stored = session.query(MarketObservation).filter_by(instrument_id=instrument.id).count()
    assert stored == 3


def test_trades_deduplicate_by_official_id_then_by_fingerprint(session, instrument):
    store = HistoryStore(session)
    moment = NOW - timedelta(days=2)
    with_id = TradeRecord(trade_timestamp=moment, price=100.0, quantity=10, trade_id="T-1")
    same_id_other_values = TradeRecord(
        trade_timestamp=moment, price=101.0, quantity=11, trade_id="T-1"
    )
    without_id = TradeRecord(trade_timestamp=moment, price=100.0, quantity=10)

    store.save_trades(instrument.id, [with_id])
    store.save_trades(instrument.id, [same_id_other_values, without_id, without_id])

    rows = session.query(HistoricalTrade).filter_by(instrument_id=instrument.id).all()
    assert len(rows) == 2, "the official id wins; the unkeyed trade gets a fingerprint"


def test_dividend_and_report_history_is_versioned_not_overwritten(session, instrument):
    store = HistoryStore(session)
    dividend = DividendRecord(amount_per_share=100.0, ex_date=date(2026, 5, 1))
    assert store.save_dividends(instrument.id, [dividend])["created"] == 1
    assert store.save_dividends(instrument.id, [dividend])["created"] == 0

    original = ReportRecord(
        reporting_period=date(2025, 12, 31), document_hash="hash-1",
        available_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    restated = ReportRecord(
        reporting_period=date(2025, 12, 31), document_hash="hash-2",
        available_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    store.save_reports(instrument.id, [original])
    store.save_reports(instrument.id, [restated])
    store.save_reports(instrument.id, [original])

    from app.models.history import FinancialReportRelease

    rows = (
        session.query(FinancialReportRelease)
        .filter_by(instrument_id=instrument.id)
        .order_by(FinancialReportRelease.version)
        .all()
    )
    assert [row.version for row in rows] == [1, 2]
    assert rows[1].is_restatement is True
    assert rows[0].document_hash == "hash-1", "the original statement is untouched"


# ---------------------------------------------------------------------------
# historical corrections (§30)
# ---------------------------------------------------------------------------


def test_a_restated_price_is_recorded_without_destroying_the_original(session, instrument):
    from app.models.history import HistoricalCorrection

    store = HistoryStore(session)
    day = date(2026, 8, 12)
    store.save_observations(instrument.id, [observation(day, 1000.0)])
    outcome = store.save_observations(instrument.id, [observation(day, 1010.0)])

    rows = (
        session.query(MarketObservation)
        .filter_by(instrument_id=instrument.id)
        .order_by(MarketObservation.id)
        .all()
    )
    corrections = session.query(HistoricalCorrection).filter_by(
        instrument_id=instrument.id
    ).all()

    assert outcome["corrections"] == 1
    assert [row.price for row in rows] == [1000.0, 1010.0], "the original is kept"
    assert rows[0].superseded_at is not None and rows[1].superseded_at is None
    assert [(c.field, c.original_value, c.corrected_value) for c in corrections] == [
        ("price", "1000.0", "1010.0")
    ]
    assert corrections[0].effective_date == day


def test_the_daily_snapshot_follows_the_correction(session, instrument):
    store = HistoryStore(session)
    day = date(2026, 8, 12)
    store.save_observations(instrument.id, [observation(day, 1000.0)])
    store.save_observations(instrument.id, [observation(day, 1010.0)])
    store.rebuild_daily_snapshots(instrument.id)

    snapshot = session.query(DailyMarketSnapshot).filter_by(trading_date=day).one()
    assert snapshot.close == 1010.0
    assert snapshot.open is None, "a corrected single price is still one price, not a candle"
    assert snapshot.observation_count == 1


def test_a_value_arriving_for_the_first_time_is_not_a_correction(session, instrument):
    from app.models.history import HistoricalCorrection

    store = HistoryStore(session)
    day = date(2026, 8, 12)
    store.save_observations(instrument.id, [observation(day, 1000.0)])
    # The same price, now with a volume the earlier page did not show.
    outcome = store.save_observations(instrument.id, [observation(day, 1000.0, volume=500.0)])
    store.rebuild_daily_snapshots(instrument.id)

    assert outcome["created"] == 1 and outcome["corrections"] == 0
    assert session.query(HistoricalCorrection).count() == 0, (
        "a field that was never published was never wrong"
    )
    snapshot = session.query(DailyMarketSnapshot).filter_by(trading_date=day).one()
    assert snapshot.observation_count == 1, "one moment keeps one current reading"
    assert snapshot.volume == 500.0
    assert snapshot.open is None, "two reads of one moment are not two prints"


# ---------------------------------------------------------------------------
# documents, news and corporate actions (§11, §13, §14)
# ---------------------------------------------------------------------------


class FakeDocument:
    """What the browser's document extractor returns, without the browser."""

    def __init__(self, url: str, name: str, publication_date: str | None = None):
        self.url = url
        self.name = name
        self.publication_date = publication_date
        self.document_type = "pdf"


def test_only_financial_documents_become_report_releases():
    documents = [
        FakeDocument("https://kase.kz/f/1.pdf", "Финансовая отчетность за 4 кв 2025", "01.03.2026"),
        FakeDocument("https://kase.kz/f/2.pdf", "Проспект выпуска облигаций", "01.03.2026"),
        FakeDocument("https://kase.kz/f/3.pdf", "Аудированная финансовая отчетность 2025"),
    ]
    reports = parse_report_documents(documents, source="kase_public_website")

    assert [row.reporting_period for row in reports] == [date(2025, 12, 31), date(2025, 12, 31)]
    assert [row.period_type for row in reports] == ["Q", "Y"]
    assert reports[1].publication_date is None, "an undated document is not given a date"
    assert reports[0].document_hash != reports[1].document_hash


def test_a_document_with_no_stated_period_is_skipped():
    assert parse_report_documents([FakeDocument("https://kase.kz/f/x.pdf", "Финансовая отчетность")]) == []


def test_report_documents_are_versioned_on_re_publication(session, instrument):
    store = HistoryStore(session)
    first = FakeDocument("https://kase.kz/f/1.pdf", "Финансовая отчетность за 2025 год", "01.03.2026")
    restated = FakeDocument("https://kase.kz/f/1r.pdf", "Финансовая отчетность за 2025 год", "01.06.2026")

    store.save_reports(instrument.id, parse_report_documents([first]))
    store.save_reports(instrument.id, parse_report_documents([restated]))
    # Re-running the backfill sees both again and stores neither twice.
    outcome = store.save_reports(instrument.id, parse_report_documents([first, restated]))

    from app.models.history import FinancialReportRelease

    rows = (
        session.query(FinancialReportRelease)
        .filter_by(instrument_id=instrument.id)
        .order_by(FinancialReportRelease.version)
        .all()
    )
    assert outcome["created"] == 0 and outcome["duplicates"] == 2
    assert [row.version for row in rows] == [1, 2]
    assert rows[0].document_url.endswith("1.pdf"), "the first release keeps its own document"


def test_publication_links_keep_only_what_the_page_stated():
    links = [
        {
            "url": "https://kase.kz/ru/news/show/1/",
            "title": "О выплате дивидендов по простым акциям",
            "publication_date": "12.05.2026",
            "context": "12.05.2026 О выплате дивидендов",
        },
        {
            "url": "https://kase.kz/ru/news/show/2/",
            "title": "О листинге акций",
            "publication_date": None,
            "context": "",
        },
    ]
    records = parse_publication_links(links, source="kase_public_website")

    assert [row.event_type for row in records] == ["dividend", "listing_change"]
    assert records[0].publication_date == datetime(2026, 5, 12, tzinfo=timezone.utc)
    assert records[1].publication_date is None, "an undated headline stays undated"


def test_publications_outside_the_window_are_not_collected():
    links = [{
        "url": "https://kase.kz/ru/news/show/9/",
        "title": "Старое сообщение эмитента о собрании акционеров",
        "publication_date": "01.01.2019",
    }]
    assert parse_publication_links(links, window_start=date(2024, 8, 17)) == []


def test_news_is_stored_once_however_often_the_backfill_reruns(session, instrument):
    from app.models.incremental import KaseNewsItem

    store = HistoryStore(session)
    records = parse_publication_links([
        {
            "url": "https://kase.kz/ru/news/show/11/",
            "title": "О решении совета директоров по дивидендам",
            "publication_date": "12.05.2026",
        },
        {
            "url": "https://kase.kz/ru/news/show/12/",
            "title": "Об изменении состава совета директоров",
            "publication_date": "13.05.2026",
        },
    ], source="kase_public_website")

    first = store.save_news(instrument.id, records, ticker=instrument.ticker)
    second = store.save_news(instrument.id, records, ticker=instrument.ticker)

    stored = session.query(KaseNewsItem).filter_by(ticker=instrument.ticker).all()
    assert first["created"] == 2
    assert second["created"] == 0 and second["duplicates"] == 2
    assert len(stored) == 2, "the same article is never stored twice"
    assert all(row.url.startswith("https://kase.kz/") for row in stored)


def test_a_dividend_headline_without_an_amount_stays_news(session, instrument):
    from app.models.incremental import KaseNewsItem
    from app.models.stock import CorporateAction, Dividend

    store = HistoryStore(session)
    outcome = store.save_news(
        instrument.id,
        parse_publication_links([{
            "url": "https://kase.kz/ru/news/show/21/",
            "title": "О предстоящей выплате дивидендов",
            "publication_date": "12.05.2026",
        }]),
        ticker=instrument.ticker,
    )
    stock_id = session.query(Stock).filter_by(instrument_id=instrument.id).one().id

    assert outcome["created"] == 1
    assert session.query(KaseNewsItem).filter_by(ticker=instrument.ticker).count() == 1
    assert session.query(Dividend).filter_by(stock_id=stock_id).count() == 0, (
        "no stated amount means no dividend record is invented"
    )
    assert session.query(CorporateAction).filter_by(stock_id=stock_id).count() == 0, (
        "the headline is preserved as news, but it is not promoted to a fact"
    )


def test_a_stated_corporate_action_is_recorded_once(session, instrument):
    from app.models.stock import CorporateAction

    store = HistoryStore(session)
    records = parse_publication_links([{
        "url": "https://kase.kz/ru/news/show/31/",
        "title": "О дроблении простых акций эмитента",
        "publication_date": "01.04.2026",
        "context": "01.04.2026 О дроблении простых акций эмитента",
    }])
    store.save_news(instrument.id, records, ticker=instrument.ticker)
    store.save_news(instrument.id, records, ticker=instrument.ticker)

    stock_id = session.query(Stock).filter_by(instrument_id=instrument.id).one().id
    rows = session.query(CorporateAction).filter_by(stock_id=stock_id).all()
    assert [row.action_type for row in rows] == ["split"]


def test_coverage_reports_the_collected_publications(session, instrument):
    store = HistoryStore(session)
    store.save_news(
        instrument.id,
        parse_publication_links([{
            "url": "https://kase.kz/ru/news/show/41/",
            "title": "О дроблении простых акций эмитента",
            "publication_date": "01.04.2026",
        }]),
        ticker=instrument.ticker,
    )
    coverage = CoverageService(session).measure(instrument, backfill_window(now=NOW, years=2))

    assert coverage.news_history_coverage == 1.0
    assert coverage.corporate_action_coverage == 1.0
    assert coverage.details["corporate_actions"] == 1


def test_an_issuer_with_no_publications_reports_no_coverage(session, instrument):
    coverage = CoverageService(session).measure(instrument, backfill_window(now=NOW, years=2))

    assert coverage.news_history_coverage is None
    assert coverage.corporate_action_coverage is None, "nothing found is not 0% and not 100%"


# ---------------------------------------------------------------------------
# daily aggregation (§5, §8)
# ---------------------------------------------------------------------------


def test_one_price_a_day_gives_a_close_and_no_manufactured_candle(session, instrument):
    store = HistoryStore(session)
    store.save_observations(instrument.id, [observation(date(2026, 8, 12), 1000.0)])
    store.rebuild_daily_snapshots(instrument.id)

    day = session.query(DailyMarketSnapshot).filter_by(trading_date=date(2026, 8, 12)).one()
    assert day.close == pytest.approx(1000.0)
    assert (day.open, day.high, day.low) == (None, None, None)
    assert day.coverage_quality == "single_price"


def test_several_prices_a_day_produce_a_real_candle(session, instrument):
    store = HistoryStore(session)
    day = date(2026, 8, 12)
    base = datetime.combine(day, datetime.min.time(), tzinfo=KASE_TZ)
    store.save_observations(
        instrument.id,
        [
            ObservationRecord(observed_at=(base.replace(hour=h)).astimezone(timezone.utc),
                              price=price, volume=10, trading_date=day, data_mode="browser")
            for h, price in ((10, 1000.0), (12, 1050.0), (16, 990.0))
        ],
    )
    store.rebuild_daily_snapshots(instrument.id)

    row = session.query(DailyMarketSnapshot).filter_by(trading_date=day).one()
    assert (row.open, row.high, row.low, row.close) == (1000.0, 1050.0, 990.0, 990.0)
    assert row.volume == pytest.approx(30)
    assert row.coverage_quality == "full"


def test_a_no_trade_day_is_recorded_as_such(session, instrument):
    store = HistoryStore(session)
    store.save_observations(
        instrument.id,
        [observation(date(2026, 8, 11), 1000.0), observation(date(2026, 8, 12), None)],
    )
    store.rebuild_daily_snapshots(instrument.id)

    quiet = session.query(DailyMarketSnapshot).filter_by(trading_date=date(2026, 8, 12)).one()
    assert quiet.status == STATUS_NO_TRADE
    assert quiet.close is None, "the previous close is never carried forward"


def test_days_never_observed_get_no_row(session, instrument):
    store = HistoryStore(session)
    store.save_observations(instrument.id, [observation(date(2026, 8, 12), 1000.0)])
    store.rebuild_daily_snapshots(instrument.id)

    assert session.query(DailyMarketSnapshot).filter_by(
        instrument_id=instrument.id
    ).count() == 1, "a missing day is a gap, not an invented row"


# ---------------------------------------------------------------------------
# queue, checkpoints, resume (§17, §21)
# ---------------------------------------------------------------------------


def test_priority_puts_held_and_watched_instruments_first(session, instrument):
    from app.models.portfolio import Portfolio, PortfolioPosition, Watchlist

    queue = BackfillQueue(session)
    stock = session.query(Stock).filter_by(instrument_id=instrument.id).one()
    assert queue.priority_for(instrument, stock) == PRIORITY_UNIVERSE

    session.add(Watchlist(anonymous_token="anon-1", stock_id=stock.id))
    session.flush()
    assert queue.priority_for(instrument, stock) == PRIORITY_WATCHLIST

    portfolio = Portfolio(name="p", anonymous_token="anon-1")
    session.add(portfolio)
    session.flush()
    session.add(PortfolioPosition(
        portfolio_id=portfolio.id, stock_id=stock.id, instrument_type="stock", quantity=1
    ))
    session.flush()
    assert queue.priority_for(instrument, stock) == PRIORITY_PORTFOLIO


def test_checkpoint_records_progress_and_survives_a_failure(session, instrument):
    window = backfill_window(now=NOW)
    queue = BackfillQueue(session)
    checkpoint = queue.enqueue(instrument, window)
    queue.start(checkpoint)
    queue.advance(checkpoint, last_timestamp=NOW - timedelta(days=200))
    queue.finish(checkpoint, status="failed", error="boom", retry_in=timedelta(seconds=1))

    reloaded = session.get(BackfillCheckpoint, checkpoint.id)
    assert reloaded.last_processed_timestamp == NOW - timedelta(days=200)
    assert reloaded.attempts == 1
    assert reloaded.last_error == "boom"


def test_a_killed_run_does_not_strand_its_instrument(session, instrument):
    """A claim nobody is working on expires and the instrument is crawled again."""
    window = backfill_window(now=NOW)
    queue = BackfillQueue(session)
    checkpoint = queue.enqueue(instrument, window)
    queue.start(checkpoint)
    # The process died here: the row stays claimed with nobody behind it.
    assert checkpoint.status == STATUS_PROCESSING
    assert queue.next_batch(now=NOW) == []

    checkpoint.updated_at = NOW - LEASE - timedelta(minutes=1)
    session.flush()
    assert [row.id for row in queue.next_batch(now=NOW)] == [checkpoint.id]


def test_a_live_run_keeps_the_instrument_it_is_working_on(session, instrument):
    window = backfill_window(now=NOW)
    queue = BackfillQueue(session)
    checkpoint = queue.enqueue(instrument, window)
    queue.start(checkpoint)
    checkpoint.updated_at = NOW - LEASE + timedelta(minutes=1)
    session.flush()
    assert queue.next_batch(now=NOW) == []


@pytest.mark.anyio
async def test_resume_passes_the_checkpoint_to_the_collector(session, instrument, anyio_backend):
    window = backfill_window(now=NOW)
    queue = BackfillQueue(session)
    checkpoint = queue.enqueue(instrument, window)
    checkpoint.last_processed_timestamp = NOW - timedelta(days=100)
    session.flush()

    collector = FakeCollector(collection([observation(date(2026, 8, 12), 1000.0)]))
    runner = BackfillRunner(session, collector=collector, window=window)
    await runner.backfill_instrument(instrument, checkpoint=checkpoint)

    assert collector.calls[0]["since"] == NOW - timedelta(days=100)
    assert collector.calls[0]["url"] == instrument.kase_url


# ---------------------------------------------------------------------------
# the runner end to end (§10, §29, §35)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_backfill_is_idempotent_end_to_end(session, instrument, anyio_backend):
    window = backfill_window(now=NOW)
    records = [observation(date(2026, 8, 10 + i), 1000.0 + i) for i in range(5)]
    collector = FakeCollector(collection(records, trades=[
        TradeRecord(trade_timestamp=NOW - timedelta(days=3), price=1000.0, quantity=5, trade_id="T-9")
    ]))
    runner = BackfillRunner(session, collector=collector, window=window)

    first = await runner.backfill_instrument(instrument)
    second = await runner.backfill_instrument(instrument)

    assert first["observations"]["created"] == 5
    assert second["observations"]["created"] == 0
    assert second["observations"]["duplicates"] == 5
    assert session.query(MarketObservation).filter_by(instrument_id=instrument.id).count() == 5
    assert session.query(HistoricalTrade).filter_by(instrument_id=instrument.id).count() == 1
    assert first["status"] in (STATUS_COMPLETED, STATUS_PARTIAL)


@pytest.mark.anyio
async def test_partial_coverage_is_reported_honestly(session, instrument, anyio_backend):
    window = backfill_window(now=NOW)
    # Four months of a two-year request.
    days = [d for d in market_days(date(2026, 4, 20), date(2026, 8, 14))]
    collector = FakeCollector(collection([observation(day, 1000.0) for day in days]))
    runner = BackfillRunner(session, collector=collector, window=window)

    result = await runner.backfill_instrument(instrument)
    coverage = result["coverage"]

    assert result["status"] == STATUS_PARTIAL
    assert coverage["status"] == "partial"
    assert coverage["market_days_covered"] == len(days)
    assert coverage["completeness"] < 0.5
    assert coverage["market_days_expected"] > len(days)


@pytest.mark.anyio
async def test_a_missing_source_is_recorded_not_invented(session, instrument, anyio_backend):
    window = backfill_window(now=NOW)
    collector = FakeCollector(collection())
    runner = BackfillRunner(session, collector=collector, window=window)

    result = await runner.backfill_instrument(instrument)

    assert result["observations"]["created"] == 0
    assert session.query(MarketObservation).filter_by(instrument_id=instrument.id).count() == 0
    assert result["coverage"]["status"] == "unavailable"


@pytest.mark.anyio
async def test_a_blocked_site_stops_the_crawl(session, instrument, anyio_backend):
    window = backfill_window(now=NOW)
    collector = FakeCollector(collection(blocked=True, notes=["captcha"]))
    runner = BackfillRunner(session, collector=collector, window=window)

    result = await runner.backfill_instrument(instrument)

    assert result["status"] == "blocked"
    checkpoint = BackfillQueue(session).get(instrument.id)
    assert checkpoint.status == "blocked"
    assert checkpoint.next_attempt_at is not None, "blocked means back off, not retry harder"


@pytest.mark.anyio
async def test_a_broken_parse_preserves_the_last_validated_history(session, instrument, anyio_backend):
    window = backfill_window(now=NOW)
    good = collection([observation(date(2026, 8, 12), 1000.0)])
    runner = BackfillRunner(session, collector=FakeCollector(good), window=window)
    await runner.backfill_instrument(instrument)

    garbage = collection([
        ObservationRecord(observed_at=NOW, price=-1.0) for _ in range(5)
    ])
    broken = BackfillRunner(session, collector=FakeCollector(garbage), window=window)
    result = await broken.backfill_instrument(instrument)

    assert result["status"] == "failed"
    rows = session.query(MarketObservation).filter_by(instrument_id=instrument.id).all()
    assert len(rows) == 1 and rows[0].price == pytest.approx(1000.0)
    assert session.query(IngestionAnomaly).filter_by(instrument_id=instrument.id).count() >= 1


@pytest.mark.anyio
async def test_collector_failure_schedules_a_retry(session, instrument, anyio_backend):
    window = backfill_window(now=NOW)
    runner = BackfillRunner(
        session, collector=FakeCollector(collection(), error=RuntimeError("timeout")),
        window=window,
    )
    result = await runner.backfill_instrument(instrument)

    assert result["status"] == "failed"
    checkpoint = BackfillQueue(session).get(instrument.id)
    assert checkpoint.attempts == 1
    assert checkpoint.next_attempt_at is not None


# ---------------------------------------------------------------------------
# discovery and lifecycle (§2, §26, §27)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_every_discovered_stock_is_enrolled(session, instrument, anyio_backend):
    runner = BackfillRunner(session, collector=FakeCollector(collection()),
                            window=backfill_window(now=NOW))
    result = runner.enrol_all()

    assert result["queued"] >= 1
    assert BackfillQueue(session).get(instrument.id) is not None
    assert result["years"] == 2


def test_a_preferred_share_is_enrolled_and_then_monitored_like_any_other(session):
    """Preferred shares are listed and traded on KASE, so they are not a special case.

    Discovery already queues them; if the runner or the monitoring pass filtered
    them out, a preferred share would be backfilled once and then never observed
    again - history that quietly stops growing.
    """
    from app.services.monitoring import MonitoringService

    unique = uuid.uuid4().hex[:8].upper()
    issuer = Issuer(name="Pref Issuer", code=f"PRISS{unique}", sector="corporate")
    session.add(issuer)
    session.flush()
    preferred = Instrument(
        ticker=f"PR{unique}p", isin=f"KZ{unique}PRF", issuer_id=issuer.id,
        instrument_type="preferred_stock", currency="KZT", is_active=True,
    )
    session.add(preferred)
    session.flush()
    session.add(Stock(instrument_id=preferred.id))
    session.flush()

    runner = BackfillRunner(session, collector=FakeCollector(collection()),
                            window=backfill_window(now=NOW))
    runner.enrol_all()

    assert BackfillQueue(session).get(preferred.id) is not None
    monitored = [row[0].id for row in MonitoringService(session).active_instruments()]
    assert preferred.id in monitored


@pytest.mark.anyio
async def test_the_public_requests_the_page_made_are_recorded(session, instrument, anyio_backend):
    """§16: observed endpoints are documented as metadata, never used as a way in."""
    endpoints = [{
        "method": "GET",
        "url": "https://kase.kz/api/public/shares/",
        "status": 200,
        "content_type": "application/json",
        "resource_type": "xhr",
        "source_page": "https://kase.kz/en/investors/shares/TEST/",
        "auth_required": False,
        "license_uncertainty": True,
    }]
    runner = BackfillRunner(
        session,
        collector=FakeCollector(
            collection(observations=[observation(date(2026, 8, 12), 1000.0)]),
            endpoints=endpoints,
        ),
        window=backfill_window(now=NOW),
    )
    await runner.backfill_instrument(instrument)

    recorded = CoverageService(session).get(instrument.id).details["observed_endpoints"]
    assert recorded == endpoints
    assert recorded[0]["source_page"], "an endpoint is only documentable with its page"


def test_a_delisted_stock_keeps_its_history(session, instrument):
    store = HistoryStore(session)
    store.save_observations(instrument.id, [observation(date(2026, 8, 12), 1000.0)])
    store.rebuild_daily_snapshots(instrument.id)

    instrument.is_active = False
    session.flush()

    runner = BackfillRunner(session, collector=FakeCollector(collection()),
                            window=backfill_window(now=NOW))
    runner.enrol_all()

    assert session.query(MarketObservation).filter_by(instrument_id=instrument.id).count() == 1
    assert session.query(DailyMarketSnapshot).filter_by(instrument_id=instrument.id).count() == 1


# ---------------------------------------------------------------------------
# charts (§22, §23)
# ---------------------------------------------------------------------------


def _seed_two_years(session, instrument, *, until: date = date(2026, 8, 14)) -> int:
    store = HistoryStore(session)
    days = market_days(shift_years(until, 2), until)
    store.save_observations(
        instrument.id,
        [observation(day, 1000.0 + index * 0.1) for index, day in enumerate(days)],
    )
    store.rebuild_daily_snapshots(instrument.id)
    return len(days)


def test_two_year_chart_uses_stored_history(session, instrument):
    seeded = _seed_two_years(session, instrument)
    payload = ChartService(session).series(instrument, range_key="2y", now=NOW)

    assert payload["range"] == "2y"
    assert payload["resolution"] == "1w", "long ranges aggregate for performance"
    assert payload["source"] == "daily_market_snapshots"
    assert 90 <= payload["points"] <= 120, payload["points"]
    assert payload["insufficient_history"]["value"] is False
    # Aggregation is a view: the underlying rows are untouched.
    assert session.query(DailyMarketSnapshot).filter_by(
        instrument_id=instrument.id
    ).count() == seeded


def test_short_history_is_flagged_as_insufficient(session, instrument):
    store = HistoryStore(session)
    days = market_days(date(2026, 6, 1), date(2026, 8, 14))
    store.save_observations(instrument.id, [observation(day, 1000.0) for day in days])
    store.rebuild_daily_snapshots(instrument.id)

    payload = ChartService(session).series(instrument, range_key="2y", now=NOW)
    assert payload["insufficient_history"]["value"] is True
    assert payload["insufficient_history"]["traded_days"] == len(days)
    assert payload["insufficient_history"]["completeness"] < 0.25


def test_chart_never_fills_gaps(session, instrument):
    store = HistoryStore(session)
    store.save_observations(
        instrument.id,
        [observation(date(2026, 8, 10), 1000.0), observation(date(2026, 8, 14), 1010.0)],
    )
    store.rebuild_daily_snapshots(instrument.id)

    payload = ChartService(session).series(instrument, range_key="1m", resolution="1d", now=NOW)
    assert payload["points"] == 2, "the three untraded days stay absent"


def test_all_ranges_answer(session, instrument):
    _seed_two_years(session, instrument)
    service = ChartService(session)
    for range_key in ("1d", "5d", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "max"):
        payload = service.series(instrument, range_key=range_key, now=NOW)
        assert payload["range"] == range_key
        assert isinstance(payload["series"], list)
        assert "coverage" in payload and "insufficient_history" in payload


def test_multi_year_ranges_are_anchored_on_the_calendar(session, instrument):
    """3Y and 5Y count whole years, so a leap day cannot shift the window."""
    from app.services.chart_service import range_start

    assert range_start("3y", now=NOW).date() == shift_years(NOW.date(), 3)
    assert range_start("5y", now=NOW).date() == shift_years(NOW.date(), 5)


def test_five_year_range_admits_it_only_holds_two(session, instrument):
    """A longer range must report the shortfall, never imply history it lacks."""
    _seed_two_years(session, instrument)
    payload = ChartService(session).series(instrument, range_key="5y", now=NOW)

    assert payload["range"] == "5y"
    assert payload["resolution"] == "1mo", "five years aggregate to months"
    assert payload["insufficient_history"]["value"] is True
    assert payload["insufficient_history"]["completeness"] < 0.5


# ---------------------------------------------------------------------------
# backfill -> live continuity (§25)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_monitoring_continues_the_history_without_a_duplicate(
    session, instrument, anyio_backend
):
    from app.models.stock import StockQuote
    from app.services.monitoring import MonitoringService

    window = backfill_window()
    # The final backfilled point is recent enough for monitoring to see it.
    moment = datetime.now(timezone.utc).replace(microsecond=0)
    last_backfilled = ObservationRecord(
        observed_at=moment, price=1010.0, status=STATUS_TRADED,
        source="kase_public_website", data_mode="browser",
        trading_date=moment.date(),
    )
    runner = BackfillRunner(
        session, collector=FakeCollector(collection([last_backfilled])), window=window
    )
    await runner.backfill_instrument(instrument)

    stock = session.query(Stock).filter_by(instrument_id=instrument.id).one()
    # The first live poll reports exactly the point the backfill ended on.
    session.add(StockQuote(
        stock_id=stock.id, timestamp=moment, last=1010.0,
        data_mode="browser", source="kase_public_website",
    ))
    session.flush()

    service = MonitoringService(session)
    same = service.record_latest(instrument, stock)
    assert same["created"] == 0, "the seam de-duplicates itself"

    # A genuinely new reading extends the history.
    session.add(StockQuote(
        stock_id=stock.id, timestamp=moment + timedelta(minutes=10), last=1015.0,
        data_mode="browser", source="kase_public_website",
    ))
    session.flush()
    fresh = service.record_latest(instrument, stock)
    assert fresh["created"] == 1

    total = session.query(MarketObservation).filter_by(instrument_id=instrument.id).count()
    assert total == 2


def test_history_is_never_deleted_for_being_old(session, instrument):
    """The two-year window bounds what we *request*, not what we keep."""
    store = HistoryStore(session)
    ancient = observation(date(2019, 3, 15), 500.0)
    store.save_observations(instrument.id, [ancient])
    store.rebuild_daily_snapshots(instrument.id)

    CoverageService(session).measure(instrument, backfill_window(now=NOW))

    assert session.query(MarketObservation).filter_by(instrument_id=instrument.id).count() == 1
    payload = ChartService(session).series(instrument, range_key="max", now=NOW)
    assert payload["points"] == 1, "MAX still shows history older than the window"


# --- the public chart feed -------------------------------------------------


def _udf(times: list[int], closes: list[float], **series) -> dict:
    return {"s": "ok", "t": times, "c": closes, **series}


def test_chart_feed_yields_one_observation_per_daily_bar():
    window = backfill_window(now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc), years=2)
    day = int(datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp())
    payload = _udf(
        [day, day + 86_400],
        [38841.0, 39078.99],
        o=[39194.49, 38880.0], h=[39194.49, 39089.0], l=[38810.01, 38801.0],
        v=[1485712.0, 6451308.0],
    )
    records = parse_history(payload, window, "https://kase.kz/tv-charts/securities/history")
    assert len(records) == 2
    first = records[0]
    assert first.close == 38841.0 and first.price == 38841.0
    assert first.open == 39194.49 and first.high == 39194.49 and first.low == 38810.01
    assert first.volume == 1485712.0
    assert first.trading_date == date(2026, 8, 24)
    assert first.source == "kase_public_chart_api" and first.data_mode == "public_api"
    assert validate_observations(records).accepted == records


def test_chart_feed_bars_outside_the_window_are_dropped():
    window = backfill_window(now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc), years=2)
    stale = int(datetime(2019, 5, 6, tzinfo=timezone.utc).timestamp())
    inside = int(datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp())
    records = parse_history(_udf([stale, inside], [100.0, 200.0]), window, "https://kase.kz/x")
    assert [record.close for record in records] == [200.0]


def test_chart_feed_refusal_and_missing_closes_produce_no_history():
    window = backfill_window(now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc), years=2)
    day = int(datetime(2026, 8, 24, tzinfo=timezone.utc).timestamp())
    assert parse_history({"s": "no_data"}, window, "https://kase.kz/x") == []
    # A bar the feed left blank is a gap, not a zero and not the previous close.
    assert parse_history(_udf([day], [None]), window, "https://kase.kz/x") == []
