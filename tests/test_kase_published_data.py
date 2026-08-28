"""What the two KASE readers may and may not conclude from a publication."""

from datetime import date

from sqlalchemy import select

from app.collectors.kase_daily_history import daily_closes, import_daily_closes
from app.collectors.kase_fundamentals import (
    KaseFundamentalsClient,
    import_fundamentals,
    parse_fin_data,
    parse_shares_outstanding,
)
from app.forecast.calendar import kase_date
from app.models.financials import FinancialStatement
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock, StockQuote


def _catalog_row(**overrides) -> dict:
    # 2026-08-24 is a Monday, so the four earlier closes walk back over a
    # weekend: Fri 21, Thu 20, Wed 19, Tue 18.
    row = {
        "code": "PUBL",
        "date0": "2026-08-24T17:31:09",
        "trand": 5.0,
        "monthly_spark_line": "100;101;102;103;108",
    }
    row.update(overrides)
    return row


def _stock(session, ticker: str = "PUBL", issuer_code: str = "ISS-PUBL") -> Stock:
    issuer = Issuer(code=issuer_code, name="Published Data Test", country="KZ")
    session.add(issuer); session.flush()
    instrument = Instrument(ticker=ticker, issuer_id=issuer.id, instrument_type="stock",
                            currency="KZT", is_active=True)
    session.add(instrument); session.flush()
    stock = Stock(instrument_id=instrument.id, lot_size=1)
    session.add(stock); session.flush()
    return stock


def test_spark_line_is_dated_backwards_over_the_trading_calendar():
    closes = daily_closes(_catalog_row())
    assert [item.trading_date for item in closes] == [
        date(2026, 8, 18), date(2026, 8, 19), date(2026, 8, 20),
        date(2026, 8, 21), date(2026, 8, 24),
    ]
    assert [item.close for item in closes] == [100.0, 101.0, 102.0, 103.0, 108.0]


def test_series_that_contradicts_the_published_change_is_refused():
    # KASE says the session moved by 5, the series implies 30: the values and
    # the dates cannot both be right, so neither is stored.
    assert daily_closes(_catalog_row(monthly_spark_line="100;101;102;78;108")) == []


def test_missing_or_unusable_publication_yields_nothing():
    assert daily_closes(_catalog_row(monthly_spark_line="")) == []
    assert daily_closes(_catalog_row(monthly_spark_line="100;abc;102")) == []
    assert daily_closes(_catalog_row(date0=None)) == []


def test_published_closes_become_history_and_repeat_for_free(session):
    stock = _stock(session, ticker="PUBLA", issuer_code="ISS-PUBLA")
    instrument = session.get(Instrument, stock.instrument_id)
    rows = [_catalog_row(code="PUBLA"), _catalog_row(code="NOTHERE")]

    preview = import_daily_closes(session, rows, dry_run=True)
    assert preview["quotes_created"] == 5 and preview["not_listed_here"] == 1
    assert session.execute(select(StockQuote).where(StockQuote.stock_id == stock.id)).scalars().all() == []

    result = import_daily_closes(session, rows)
    assert result["quotes_created"] == 5 and result["observations_created"] == 5
    quotes = list(session.execute(select(StockQuote).where(
        StockQuote.stock_id == stock.id).order_by(StockQuote.timestamp)).scalars())
    assert [kase_date(row.timestamp) for row in quotes][0] == date(2026, 8, 18)
    assert quotes[-1].close == 108.0 and quotes[-1].source == "kase_public_api"
    assert instrument.ticker in (quotes[-1].source_url or "")

    repeated = import_daily_closes(session, rows)
    assert repeated["quotes_created"] == 0 and repeated["already_held"] == 5


def test_only_the_requested_sessions_are_imported(session):
    _stock(session, ticker="PUBLB", issuer_code="ISS-PUBLB")
    result = import_daily_closes(session, [_catalog_row(code="PUBLB")], since=date(2026, 8, 21))
    assert result["quotes_created"] == 2


FIN_DATA = [
    {"change_date": "2026-01-01", "units": "mln", "currency": "KZT", "audited": True,
     "volume_sale": 1_000.0, "net_profit": 200.0, "own_capital": 3_000.0,
     "aggregate_assets": 5_000.0, "total_liabilities": 2_000.0, "gross_income": 450.0},
    {"change_date": "2025-07-01", "units": "mln", "currency": "KZT", "audited": False,
     "volume_sale": 400.0, "net_profit": 70.0, "own_capital": 2_800.0,
     "aggregate_assets": 4_700.0, "total_liabilities": 1_900.0, "gross_income": 180.0},
    {"change_date": "2025-05-14", "units": "mln", "currency": "KZT", "volume_sale": 1.0},
]


def test_reporting_rows_close_the_period_before_the_date_they_carry():
    periods = parse_fin_data(FIN_DATA)
    # The mid-month row names no period, so it is dropped rather than guessed.
    assert [(item.period_end, item.period_type) for item in periods] == [
        (date(2025, 6, 30), "H1"), (date(2025, 12, 31), "FY"),
    ]
    annual = periods[-1]
    assert annual.revenue == 1_000e6 and annual.total_equity == 3_000e6
    assert annual.fiscal_year == 2025 and annual.is_audited is True


def test_a_restated_period_is_stored_once(session):
    """KASE can carry the same period twice; the database must not."""
    restated = [FIN_DATA[0], dict(FIN_DATA[0], audited=False, net_profit=190.0), *FIN_DATA[1:]]
    periods = parse_fin_data(restated)
    assert [item.period_end for item in periods] == [date(2025, 6, 30), date(2025, 12, 31)]
    assert periods[-1].net_profit == 200e6

    _stock(session, ticker="REST", issuer_code="ISS-REST")
    result = import_fundamentals(session, tickers=["REST"], client=_FakeClient(restated))
    assert result["statements_created"] == 2
    assert import_fundamentals(session, tickers=["REST"], client=_FakeClient(restated))[
        "statements_created"] == 0


def test_scale_and_currency_are_read_from_the_row_that_states_them():
    thousands = dict(FIN_DATA[0], units="thnd", volume_sale=240_073_165.0)
    assert parse_fin_data([thousands])[0].revenue == 240_073_165_000.0

    dollars = dict(FIN_DATA[0], currency="долларов")
    assert parse_fin_data([dollars])[0].currency == "USD"

    # A scale or a currency the reader cannot name would put the figure off by
    # orders of magnitude, so the row is dropped instead of guessed.
    assert parse_fin_data([dict(FIN_DATA[0], units="squillions")]) == []
    assert parse_fin_data([dict(FIN_DATA[0], units=None)]) == []
    assert parse_fin_data([dict(FIN_DATA[0], currency="галактических кредитов")]) == []


def test_gross_income_is_never_stored_as_operating_profit(session):
    """KASE publishes no operating profit, so the model must keep saying so."""
    stock = _stock(session, ticker="FUND", issuer_code="ISS-FUND")
    client = _FakeClient()
    result = import_fundamentals(session, tickers=["FUND"], client=client)

    assert result["statements_created"] == 2 and result["shares_outstanding_set"] == 1
    assert stock.shares_outstanding == 12_345_678
    statement = session.execute(select(FinancialStatement).where(
        FinancialStatement.issuer_id == session.get(Instrument, stock.instrument_id).issuer_id,
    ).order_by(FinancialStatement.period_end.desc()).limit(1)).scalar_one()
    assert statement.revenue == 1_000e6
    assert statement.operating_profit is None
    assert statement.cash_and_equivalents is None
    assert statement.total_debt is None
    assert statement.capex is None
    assert statement.source == "kase_public_api"


def test_shares_outstanding_is_read_from_the_published_issue_table():
    page = '{"label":"Number of shares outstanding","value":"259 356 608"}'
    assert parse_shares_outstanding(page) == 259_356_608
    assert parse_shares_outstanding('{"label":"Face value","value":"1 000,00"}') is None
    assert parse_shares_outstanding("") is None


class _FakeClient(KaseFundamentalsClient):
    """The published surfaces, without the network."""

    def __init__(self, payload: list[dict] | None = None):  # noqa: D107 - opens no client
        self.base_url = "https://kase.kz"
        self.payload = payload if payload is not None else FIN_DATA

    def close(self) -> None:
        return None

    def fin_data(self, org_code: str):
        return parse_fin_data(self.payload)

    def shares_outstanding(self, ticker: str):
        return 12_345_678.0
