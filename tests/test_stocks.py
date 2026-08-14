from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.calculations.stock_math import Commission, calculate_stock_investment, dividend_yield, ev_ebitda, pb, pe, roa, roe
from app.collectors.kase_stock_catalog import KaseStockCatalogCollector
from app.scoring.stocks import calculate_scores


def catalog_row(**overrides):
    row = {
        "code": "TEST", "sec_type": "share", "fin_sec_en": "shares", "currency_type": "KZT",
        "org_code": "TST", "org_name_en": "Test Company", "org_short_name_en": "Test",
        "price": 125.0, "close_price": 120.0, "best_bid": 124.0, "best_offer": 126.0,
        "volkzt": 5_000_000, "dealcnt": 25, "liquid_class": 1, "date0": "2026-08-14T12:00:00",
        "subcategory_name_en": "ordinary share", "board_en": "main",
        "volume_release_number": 10_000_000,
        "ticker": {"nin": "KZ1C00000000", "excl_date": None, "securities_list_en": "official", "open_trade_date": "2020-01-01"},
    }
    row.update(overrides)
    return row


def test_stock_catalog_parsing_is_dynamic_and_rejects_delisted():
    preferred = catalog_row(code="TESTp", subcategory_name_en="preferred share", ticker={"nin": "KZ1P00000000", "excl_date": None})
    delisted = catalog_row(code="OLD", ticker={"nin": "KZ1C00000001", "finish_date": "2025-01-01"})
    global_row = catalog_row(code="AAPL_KZ", fin_sec_en="KASE Global")
    items = KaseStockCatalogCollector.parse_catalog([catalog_row(), preferred, delisted, global_row])
    assert [item["ticker"] for item in items] == ["TEST", "TESTp"]
    assert items[0]["shares_outstanding"] == 10_000_000
    assert items[1]["instrument_type"] == "preferred_stock"


@pytest.mark.parametrize("func,args,expected", [
    (pe, (100.0, 10.0), 10.0), (pb, (100.0, 50.0), 2.0),
    (roe, (20.0, 100.0), 0.2), (roa, (20.0, 400.0), 0.05),
    (dividend_yield, (8.0, 100.0), 0.08),
])
def test_stock_financial_metrics(func, args, expected):
    assert func(*args) == pytest.approx(expected)


def test_ev_ebitda_is_never_applied_to_bank():
    assert ev_ebitda(1000, 200, 100, 100) == 11
    assert ev_ebitda(1000, 200, 100, 100, is_bank=True) is None


def test_missing_and_invalid_valuation_stays_null():
    assert pe(100, None) is None
    assert pe(100, -5) is None
    assert pb(100, 0) is None


def test_amount_quantity_lot_and_percent_commission():
    result = calculate_stock_investment(identifier="TEST", amount=5_000, price=101, price_type="ask", lot_size=10,
                                        commission=Commission("percent", 0.1), trailing_dividend_per_share=5, scenario_price=111.1)
    assert result["quantity"] == 40
    assert result["principal_cost"] == 4040
    assert result["commission"] == pytest.approx(4.04)
    assert result["total_purchase_cost"] <= 5000
    assert result["dividend_income_trailing"] == 200
    assert result["scenario_profit"] is not None


def test_liquidity_warning_and_missing_price_are_explicit():
    result = calculate_stock_investment(identifier="TEST", amount=500_000, price=None, price_type=None, liquidity_warning="Низкая ликвидность")
    assert result["quantity"] == 0
    assert result["unit_price"] is None
    assert "Низкая ликвидность" in result["warnings"]


def test_stock_scores_are_separate_null_aware_and_versioned():
    scores = calculate_scores({"roe": .22, "roa": .08, "net_margin": .18, "fcf_yield": .09, "net_debt_to_equity": .2,
                               "pe": 8, "pb": 1.2, "ev_ebitda": 5, "trailing_dividend_yield": .07,
                               "revenue_growth": .15, "earnings_growth": .2, "liquidity_class": 1, "spread_pct": .01, "turnover": 20_000_000})
    assert set(scores) == {"investment", "quality", "valuation", "growth", "dividend", "liquidity", "momentum", "risk", "data_quality", "personal"}
    assert scores["investment"]["version"].startswith("stock-")
    missing = calculate_scores({})
    assert missing["quality"]["value"] is None
    assert missing["data_quality"]["value"] < scores["data_quality"]["value"]


def _seed_stock(session, ticker="TSTX"):
    from app.models.instrument import Instrument
    from app.models.issuer import Issuer
    from app.models.stock import Dividend, Stock, StockFinancialPeriod, StockQuote
    issuer = Issuer(code=f"{ticker}I", name="Test Issuer", short_name="Test", country="KZ", is_active=True)
    session.add(issuer); session.flush()
    instrument = Instrument(ticker=ticker, isin="KZ1C00009999", issuer_id=issuer.id, instrument_type="stock", security_type="ordinary share", currency="KZT", is_active=True, kase_url=f"https://kase.kz/en/investors/instruments/shares/{ticker}")
    session.add(instrument); session.flush()
    stock = Stock(instrument_id=instrument.id, shares_outstanding=1_000_000, lot_size=1, liquidity_class=1)
    session.add(stock); session.flush()
    session.add(StockQuote(stock_id=stock.id, timestamp=datetime.now(timezone.utc), bid=99, ask=101, last=100, turnover=10_000_000, number_of_trades=20, data_mode="delayed", source="kase_public_website"))
    session.add(StockFinancialPeriod(stock_id=stock.id, period_end=date(2025, 12, 31), period_type="FY", revenue=500_000_000, ebitda=100_000_000, net_income=50_000_000, total_assets=1_000_000_000, total_equity=400_000_000, total_debt=100_000_000, cash=50_000_000, free_cash_flow=60_000_000, eps=50, shares_outstanding=1_000_000))
    session.add(Dividend(stock_id=stock.id, record_date=date(2026, 5, 1), payment_date=date(2026, 6, 1), dividend_per_share=8, status="paid", currency="KZT", source="kase_public_website"))
    session.flush(); return stock


def test_stock_detail_calculator_recommend_and_compare_api(session, client):
    stock = _seed_stock(session); session.commit()
    detail = client.get("/api/v1/stocks/TSTX")
    assert detail.status_code == 200
    body = detail.json(); assert "ytm" not in body["metrics"] and body["metrics"]["pe"] == 2
    calc = client.post("/api/v1/stocks/TSTX/investment-calculation", json={"amount": 500_000, "commission": {"type": "percent", "value": .1}, "scenario": "good"})
    assert calc.status_code == 200 and calc.json()["calculation_price_type"] == "ask"
    rec = client.post("/api/v1/stocks/recommend", json={"amount": 500_000, "profile": "balanced", "limit": 5})
    assert rec.status_code == 200 and any(row["ticker"] == "TSTX" for row in rec.json()["items"])
    # A second independent stock makes the compare contract exercise its 2..10 validation.
    _seed_stock(session, "TSTY"); session.commit()
    compare = client.post("/api/v1/stocks/compare", json={"identifiers": ["TSTX", "TSTY"], "amount": 500_000})
    assert compare.status_code == 200 and len(compare.json()["columns"]) == 2


def test_incremental_stock_section_does_not_duplicate_unchanged_state(session):
    from app.services.incremental import IncrementalStateService
    service = IncrementalStateService(session)
    first = service.process(entity_type="stock", entity_id="999", section="dividends", payload={"dividends": [{"amount": 10}]}, source_url="https://kase.kz/test", ticker="TEST")
    second = service.process(entity_type="stock", entity_id="999", section="dividends", payload={"dividends": [{"amount": 10}]}, source_url="https://kase.kz/test", ticker="TEST")
    assert first.status == "created" and second.status == "unchanged"
