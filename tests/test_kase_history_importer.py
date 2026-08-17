from datetime import date

from sqlalchemy import select

from app.collectors.kase_history_importer import import_deals_csv, parse_deals_csv
from app.forecast.calendar import kase_date
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock, StockQuote


CSV = """Date;Time;Inst_Type;Sec_Type;Issuer_rus;Issuer_eng;Symbol;ISIN;T_Type;Currency;Market_Sector;P_type;Price;Volume;Value_KZT
03.01.2024;10:00:00;E;2;Тест;Test;HIST;KZ1C00009991;T;KZT;1;P;100,00;10;1000
03.01.2024;12:00:00;E;2;Тест;Test;HIST;KZ1C00009991;T;KZT;1;P;105,00;5;525
03.01.2024;16:00:00;E;2;Тест;Test;HIST;KZ1C00009991;T;KZT;1;P;102,00;7;714
04.01.2024;11:00:00;E;2;Тест;Test;HIST;KZ1C00009991;N;KZT;1;P;999,00;1;999
04.01.2024;15:00:00;E;2;Тест;Test;HIST;KZ1C00009991;T;KZT;1;P;103,00;4;412
"""


def _stock(session) -> Stock:
    issuer = Issuer(code="ISS-HIST", name="History Import Test", country="KZ")
    session.add(issuer); session.flush()
    instrument = Instrument(ticker="HIST", isin="KZ1C00009991", issuer_id=issuer.id,
                            instrument_type="stock", currency="KZT", is_active=True)
    session.add(instrument); session.flush()
    stock = Stock(instrument_id=instrument.id, lot_size=1)
    session.add(stock); session.flush()
    return stock


def test_official_deals_csv_is_aggregated_to_regular_market_ohlcv(tmp_path):
    path = tmp_path / "deals.csv"
    path.write_text(CSV, encoding="utf-8-sig")
    bars, metadata = parse_deals_csv(path)
    assert metadata["accepted_deals"] == 4
    assert len(bars) == 2
    first = bars[0]
    assert (first.open, first.high, first.low, first.close) == (100.0, 105.0, 100.0, 102.0)
    assert first.volume == 22
    assert first.turnover == 2239
    assert first.trades == 3
    assert bars[1].close == 103.0


def test_history_import_defaults_to_dry_run_and_is_idempotent(session, tmp_path):
    stock = _stock(session)
    path = tmp_path / "licensed-deals.csv"
    path.write_text(CSV, encoding="utf-8-sig")
    preview = import_deals_csv(session, path)
    assert preview["dry_run"] is True and preview["created"] == 2
    assert session.execute(select(StockQuote).where(StockQuote.stock_id == stock.id)).scalars().all() == []

    result = import_deals_csv(session, path, dry_run=False)
    assert result["created"] == 2 and result["unknown_tickers"] == 0
    quotes = list(session.execute(select(StockQuote).where(
        StockQuote.stock_id == stock.id
    ).order_by(StockQuote.timestamp)).scalars())
    assert [kase_date(row.timestamp) for row in quotes] == [date(2024, 1, 3), date(2024, 1, 4)]
    assert quotes[0].source == "kase_licensed_archive"
    assert quotes[0].close == 102.0 and quotes[0].number_of_trades == 3

    repeated = import_deals_csv(session, path, dry_run=False)
    assert repeated["unchanged"] == 2 and repeated["created"] == repeated["updated"] == 0
