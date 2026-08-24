from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
import uuid

from app.calculations.stock_math import Commission, calculate_stock_investment, dividend_yield, ev_ebitda, market_history_metrics, pb, pe, roa, roe
from app.collectors.kase_stock_catalog import KaseStockCatalogCollector
from app.scoring.stocks import calculate_scores
from app.services.stock_actions import StockActionIngestionService, parse_public_kase_action
from app.services.stock_ranking import rank_stocks


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


def test_public_trade_results_keep_real_ohlc_and_volume():
    parsed = KaseStockCatalogCollector.parse_trade_results([{
        "code": "TEST", "change_date": "2026-08-24T17:30:58",
        "openprice": 120, "max_price": 128, "min_price": 119,
        "last_deal_price": 125, "current_bid": 124, "current_offer": 126,
        "vol": 500, "volkzt": 62_500, "dealcnt": 25,
    }])

    assert parsed["TEST"]["open"] == 120
    assert parsed["TEST"]["high"] == 128
    assert parsed["TEST"]["low"] == 119
    assert parsed["TEST"]["close"] == 125
    assert parsed["TEST"]["volume"] == 500
    assert parsed["TEST"]["number_of_trades"] == 25


@pytest.mark.anyio
async def test_market_snapshot_updates_official_company_name(session):
    from app.models.issuer import Issuer

    issuer = Issuer(
        code="TST", name="Old legal name", short_name="Old name",
        country="KZ", is_active=True,
    )
    session.add(issuer)
    session.commit()

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [catalog_row()]

    class Client:
        async def get(self, *_args, **_kwargs):
            return Response()

    result = await KaseStockCatalogCollector(session, client=Client()).collect(
        deep=False
    )

    session.refresh(issuer)
    assert result["depth"] == "market_snapshot"
    assert issuer.name == "Test Company"
    assert issuer.short_name == "Test"


@pytest.mark.anyio
async def test_a_share_that_leaves_the_catalog_is_dated_not_deleted(session):
    """§27: delisting stops the crawling, never the history."""
    from app.models.history import MarketObservation
    from app.models.instrument import Instrument
    from app.models.stock import Stock

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def __init__(self, payload):
            self.payload = payload

        async def get(self, *_args, **_kwargs):
            return Response(self.payload)

    listed = catalog_row(code="GONE", ticker={"nin": "KZ1C00009999", "excl_date": None,
                                              "securities_list_en": "official",
                                              "open_trade_date": "2020-01-01"})
    await KaseStockCatalogCollector(session, client=Client([listed])).collect(deep=False)

    instrument = session.query(Instrument).filter_by(ticker="GONE").one()
    stock = session.query(Stock).filter_by(instrument_id=instrument.id).one()
    session.add(
        MarketObservation(
            instrument_id=instrument.id,
            observed_at=datetime(2026, 8, 12, 11, tzinfo=timezone.utc),
            trading_date=date(2026, 8, 12), price=100.0, status="traded",
            data_mode="browser", fingerprint="delisting-fixture",
            source="kase_public_website", source_url="https://kase.kz/",
        )
    )
    session.commit()

    # The next catalogue reports it finished, and the row itself carries the date.
    delisted = catalog_row(code="GONE", ticker={"nin": "KZ1C00009999",
                                                "finish_date": "2026-08-14"})
    survivor = catalog_row(code="STILL", ticker={"nin": "KZ1C00008888", "excl_date": None,
                                                 "securities_list_en": "official"})
    await KaseStockCatalogCollector(
        session, client=Client([delisted, survivor])
    ).collect(deep=False)

    session.refresh(instrument)
    session.refresh(stock)
    assert instrument.is_active is False
    assert stock.delisted_at == date(2026, 8, 14), "the date KASE stated, not today"
    assert session.query(MarketObservation).filter_by(
        instrument_id=instrument.id, fingerprint="delisting-fixture"
    ).count() == 1, "history survives the delisting"


def _ranking_item(
    ticker: str,
    *,
    score: float,
    timestamp: datetime,
    turnover: float = 0,
    company_name: str | None = None,
) -> dict:
    return {
        "ticker": ticker,
        "company_name": company_name or f"{ticker} issuer",
        "price": 100.0,
        "source": "kase_public_website",
        "data_timestamp": timestamp.isoformat(),
        "metrics": {"turnover": turnover},
        "scores": {
            "investment": {"value": score, "confidence": 0.8},
            "liquidity": {"value": score, "confidence": 0.8},
            "risk": {"value": 100 - score, "confidence": 0.8},
        },
    }


def test_stock_ranking_excludes_obsolete_quotes_and_moves_real_leader():
    latest = datetime(2026, 8, 14, tzinfo=timezone.utc)
    stale = _ranking_item(
        "OLD", score=99, timestamp=latest - timedelta(days=365),
    )
    first = _ranking_item(
        "KZAP", score=82, timestamp=latest, company_name="Kazatomprom",
    )
    second = _ranking_item(
        "HSBK", score=75, timestamp=latest, company_name="Halyk Bank",
    )

    initial = rank_stocks([stale, first, second], "best", 10)
    assert [item["ticker"] for item in initial["items"]] == ["KZAP", "HSBK"]
    assert initial["items"][0]["company_name"] == "Kazatomprom"

    second["scores"]["investment"]["value"] = 90
    updated = rank_stocks([stale, first, second], "best", 10)
    assert [item["ticker"] for item in updated["items"]] == ["HSBK", "KZAP"]
    assert updated["items"][0]["company_name"] == "Halyk Bank"


def test_liquidity_ranking_breaks_equal_scores_by_real_turnover():
    latest = datetime(2026, 8, 14, tzinfo=timezone.utc)
    quiet = _ranking_item("QUIET", score=70, timestamp=latest, turnover=10_000)
    active = _ranking_item("ACTIVE", score=70, timestamp=latest, turnover=5_000_000)

    result = rank_stocks([quiet, active], "liquid", 10)

    assert [item["ticker"] for item in result["items"]] == ["ACTIVE", "QUIET"]
    assert result["source"] == "KASE"
    assert result["data_mode"] == "end_of_day"


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


def test_momentum_uses_observed_history_without_forecasting():
    result = market_history_metrics([100, 110, 88, 99])
    assert result["price_trend"] == pytest.approx(-0.01)
    assert result["max_drawdown"] == pytest.approx(0.20)
    assert result["volatility"] is not None
    assert market_history_metrics([100])["price_trend"] is None


def _seed_stock(session, ticker="TSTX"):
    from app.models.instrument import Instrument
    from app.models.issuer import Issuer
    from app.models.stock import Dividend, Stock, StockFinancialPeriod, StockQuote
    issuer = Issuer(code=f"{ticker}I", name="Test Issuer", short_name="Test", country="KZ", is_active=True)
    session.add(issuer); session.flush()
    instrument = Instrument(ticker=ticker, isin="KZ1C00009999", issuer_id=issuer.id, instrument_type="stock", security_type="ordinary share", currency="KZT", is_active=True, kase_url=f"https://kase.kz/en/investors/shares/{ticker}/")
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
    quantity_calc = client.post(
        "/api/v1/stocks/TSTX/investment-calculation",
        json={"mode": "quantity", "quantity": 100, "commission": {"type": "fixed", "value": 500}},
    )
    assert quantity_calc.status_code == 200
    assert quantity_calc.json()["input_mode"] == "quantity"
    assert quantity_calc.json()["requested_quantity"] == 100
    assert quantity_calc.json()["quantity"] == 100
    rec = client.post("/api/v1/stocks/recommend", json={"amount": 500_000, "profile": "balanced", "limit": 5})
    assert rec.status_code == 200 and any(row["ticker"] == "TSTX" for row in rec.json()["items"])
    # A second independent stock makes the compare contract exercise its 2..10 validation.
    _seed_stock(session, "TSTY"); session.commit()
    compare = client.post("/api/v1/stocks/compare", json={"identifiers": ["TSTX", "TSTY"], "amount": 500_000})
    assert compare.status_code == 200 and len(compare.json()["columns"]) == 2
    analysis = client.get("/api/v1/stocks/TSTX/analysis", params={"question": "Эта акция точно вырастет?"})
    assert analysis.status_code == 200
    assert analysis.json()["answer"] == "Точную будущую цену определить невозможно. Я могу показать текущую оценку компании и сценарии изменения цены."


def test_top_stock_api_returns_official_ticker_and_live_company_name(session, client):
    _seed_stock(session, "LIVE")
    session.commit()

    response = client.get("/api/v1/stocks/top?category=best&limit=10")

    assert response.status_code == 200
    body = response.json()
    item = next(row for row in body["items"] if row["ticker"] == "LIVE")
    assert item["company_name"] == "Test"
    assert item["source"] == "kase_public_website"
    assert body["source"] == "KASE"
    assert body["latest_market_timestamp"] is not None


def test_offline_snapshot_carries_real_equity_inputs(session, tmp_path):
    import json

    from app.collectors.snapshot import export_snapshot

    _seed_stock(session, "SNAP")
    session.commit()
    path = tmp_path / "market.json"

    result = export_snapshot(session, path, note="equity round-trip fixture")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert result["stocks"] >= 1
    assert result["stock_quotes"] >= 1
    assert result["stock_financials"] >= 1
    assert any(
        row["instrument"]["ticker"] == "SNAP" for row in payload["stocks"]
    )


def test_offline_snapshot_preserves_up_to_150_real_stock_sessions(session, tmp_path):
    import json

    from app.collectors.snapshot import export_snapshot
    from app.models.history import DailyMarketSnapshot

    stock = _seed_stock(session, "HIST150")
    for offset in range(155):
        day = date(2026, 1, 1) + timedelta(days=offset)
        session.add(DailyMarketSnapshot(
            instrument_id=stock.instrument_id, trading_date=day,
            close=100 + offset, status="traded", data_mode="end_of_day",
            coverage_quality="single_price", observation_count=1,
            source="kase_public_website", source_url="https://kase.kz/",
        ))
    session.commit()
    path = tmp_path / "history.json"

    result = export_snapshot(session, path)
    rows = [row for row in json.loads(path.read_text(encoding="utf-8"))["stock_history"]
            if row["ticker"] == "HIST150"]

    assert len(rows) == 150
    assert result["stock_history"] >= 150
    assert rows[0]["trading_date"]["__d__"] == "2026-01-06"
    assert rows[-1]["trading_date"]["__d__"] == "2026-06-04"


def test_stock_peers_and_cross_asset_compare_keep_models_separate(session, client, seeded):
    from sqlalchemy import select
    from app.models.bond import Bond
    first = _seed_stock(session, "PEERX")
    second = _seed_stock(session, "PEERY")
    first.sector = second.sector = "Technology"
    first.industry = second.industry = "Software"
    bond = session.scalar(select(Bond).limit(1))
    session.commit()
    peers = client.get("/api/v1/stocks/PEERX/peers").json()
    assert peers["peer_group"]["industry"] == "Software"
    assert [row["ticker"] for row in peers["peers"]] == ["PEERY"]
    result = client.post("/api/v1/instruments/compare", json={"instruments": [
        {"identifier": "PEERX", "instrument_type": "stock"},
        {"identifier": bond.ticker, "instrument_type": "bond"},
    ]})
    assert result.status_code == 200
    rows = result.json()["items"]
    stock_row = next(row for row in rows if row["instrument_type"] == "stock")
    bond_row = next(row for row in rows if row["instrument_type"] == "bond")
    assert "ytm" not in stock_row["potential_income"]
    assert "ytm" in bond_row["potential_income"]


def test_incremental_stock_section_does_not_duplicate_unchanged_state(session):
    from app.services.incremental import IncrementalStateService
    service = IncrementalStateService(session)
    first = service.process(entity_type="stock", entity_id="999", section="dividends", payload={"dividends": [{"amount": 10}]}, source_url="https://kase.kz/test", ticker="TEST")
    second = service.process(entity_type="stock", entity_id="999", section="dividends", payload={"dividends": [{"amount": 10}]}, source_url="https://kase.kz/test", ticker="TEST")
    assert first.status == "created" and second.status == "unchanged"


def test_public_kase_dividend_parser_rejects_unverified_and_missing_amount():
    assert parse_public_kase_action({"title": "Dividend announced", "url": "https://example.com/news/1", "dividend_per_share": 10}) is None
    assert parse_public_kase_action({"title": "Дивиденды объявлены", "url": "https://kase.kz/ru/information/news/show/1"}) is None
    parsed = parse_public_kase_action({
        "title": "Акционеры приняли решение о выплате дивидендов",
        "content": "Размер дивиденда на одну простую акцию – 4 268,37 тенге.",
        "url": "https://kase.kz/ru/information/news/show/1567445",
        "record_date": "2026-06-09", "payment_date": "2026-06-10",
        "publication_date": "2026-05-28T15:34:00+05:00",
    })
    assert parsed is not None
    assert parsed["dividend_per_share"] == pytest.approx(4268.37)
    assert parsed["record_date"] == date(2026, 6, 9)
    assert parsed["status"] == "announced"
    assert parsed["source_timestamp"].isoformat() == "2026-05-28T15:34:00+05:00"
    paid = parse_public_kase_action({
        "title": "Информация о фактической выплате дивидендов",
        "content": "Банк сообщает о завершении выплаты дивидендов по простым акциям за 2024 год в размере 21,00 тенге на одну простую акцию.",
        "url": "https://kase.kz/files/emitters/HSBK/hsbk_dividends_231225_1.pdf",
    })
    assert paid is not None and paid["status"] == "paid"
    assert paid["dividend_per_share"] == 21


def test_stock_action_ingestion_is_idempotent_and_updates_incremental_sections(session):
    stock = _seed_stock(session, "ACTX")
    item = {"title": "Dividend per common share KZT 12.50", "url": "https://kase.kz/en/information/news/show/123",
            "dividend_per_share": 12.5, "record_date": "2026-07-01", "publication_date": "2026-06-01T10:00:00Z"}
    service = StockActionIngestionService(session)
    first = service.ingest(ticker=stock.instrument.ticker, items=[item])
    second = service.ingest(ticker=stock.instrument.ticker, items=[item])
    assert first == {"dividends_created": 1, "corporate_actions_created": 1}
    assert second == {"dividends_created": 0, "corporate_actions_created": 0}


def test_two_actions_sharing_one_source_url_do_not_collide(session):
    """One KASE publication can announce two things.

    Sessions run with autoflush off, so the row added for the first item is not
    visible to the second item's lookup. Before this was handled, the second
    insert hit `uq_stock_corporate_action_source` and aborted the whole
    backfill pass for that instrument.
    """
    stock = _seed_stock(session, "DUPX")
    url = "https://kase.kz/en/information/news/show/999"
    items = [
        {"title": "Dividend per common share KZT 12.50", "url": url,
         "dividend_per_share": 12.5, "record_date": "2026-07-01",
         "publication_date": "2026-06-01T10:00:00Z"},
        {"title": "Dividend per common share KZT 13.00", "url": url,
         "dividend_per_share": 13.0, "record_date": "2026-07-02",
         "publication_date": "2026-06-01T10:00:00Z"},
    ]
    result = StockActionIngestionService(session).ingest(
        ticker=stock.instrument.ticker, items=items
    )
    session.flush()

    # The pair collapses onto one row, and the last reading wins.
    assert result["corporate_actions_created"] == 1
    assert result["dividends_created"] == 1
    stored = [row for row in stock.corporate_actions if row.source_url == url]
    assert len(stored) == 1


def test_stock_watchlist_is_separate_from_bond_watchlist(session, client):
    stock = _seed_stock(session, "WSTX")
    session.commit()
    headers = {"X-Anon-Token": uuid.uuid4().hex}
    created = client.post("/api/v1/watchlist", json={"stock": stock.instrument.ticker, "instrument_type": "stock"}, headers=headers)
    assert created.status_code == 201 and created.json()["instrument_type"] == "stock"
    items = client.get("/api/v1/watchlist", headers=headers).json()["items"]
    assert [(item["ticker"], item["instrument_type"]) for item in items] == [("WSTX", "stock")]
    assert client.delete("/api/v1/watchlist/WSTX?instrument_type=stock", headers=headers).status_code == 204


def test_stock_alert_lifecycle_for_anonymous_owner(session, client):
    stock = _seed_stock(session, "ALRX")
    session.commit()
    headers = {"X-Anon-Token": uuid.uuid4().hex}
    created = client.post("/api/v1/alerts", json={"stock": stock.instrument.ticker, "kind": "price_above", "threshold": 120}, headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["instrument_type"] == "stock" and body["threshold"] == 120
    assert client.get("/api/v1/alerts", headers=headers).json()["items"][0]["ticker"] == "ALRX"
    assert client.put(f"/api/v1/alerts/{body['id']}", json={"is_active": False}, headers=headers).json()["is_active"] is False
    assert client.delete(f"/api/v1/alerts/{body['id']}", headers=headers).status_code == 204


def test_stock_price_alert_triggers_from_incremental_change(session):
    from app.models.portfolio import Alert
    from app.services.change_alerts import ChangeAlertEngine
    from app.services.incremental import IncrementalStateService
    stock = _seed_stock(session, "TRGX")
    alert = Alert(user_id=None, anonymous_token="trigger-test", bond_id=None, stock_id=stock.id,
                  instrument_type="stock", kind="price_above", threshold=120, is_active=True)
    session.add(alert); session.flush()
    states = IncrementalStateService(session)
    states.process(entity_type="stock", entity_id=str(stock.id), ticker="TRGX", section="quote",
                   payload={"price": 100}, source_url="https://kase.kz/api/instruments/securities/")
    states.process(entity_type="stock", entity_id=str(stock.id), ticker="TRGX", section="quote",
                   payload={"price": 130}, source_url="https://kase.kz/api/instruments/securities/")
    assert ChangeAlertEngine(session).evaluate_since(datetime.now(timezone.utc) - timedelta(minutes=1)) == 1
    assert alert.last_triggered_at is not None


def test_natural_language_filters_are_explicit_and_validated(session, client):
    _seed_stock(session, "NLTX")
    session.commit()
    response = client.post("/api/v1/stocks/search", json={"query": "P/E ниже 5, ROE выше 10% и с дивидендами", "limit": 10})
    assert response.status_code == 200
    body = response.json()
    assert body["validated_filters"] == {"max_pe": 5.0, "min_roe": 0.1, "min_dividend_yield": 0.000001}
    assert any(item["ticker"] == "NLTX" for item in body["items"])


def test_stock_history_and_financial_change_analysis(session, client):
    from app.models.stock import StockFinancialPeriod, StockQuote
    stock = _seed_stock(session, "HSTX")
    session.add_all([
        StockFinancialPeriod(stock_id=stock.id, period_end=date(2025, 6, 30), period_type="Q2", revenue=300, net_income=30, total_equity=200, total_debt=80, operating_cash_flow=25),
        StockFinancialPeriod(stock_id=stock.id, period_end=date(2026, 3, 31), period_type="Q1", revenue=350, net_income=35, total_equity=220, total_debt=75, operating_cash_flow=30),
        StockFinancialPeriod(stock_id=stock.id, period_end=date(2026, 6, 30), period_type="Q2", revenue=420, net_income=50, total_equity=250, total_debt=70, operating_cash_flow=45),
        StockQuote(stock_id=stock.id, timestamp=datetime(2026, 8, 15, tzinfo=timezone.utc), last=105, close=105, data_mode="end_of_day", source="kase_public_website"),
    ])
    session.commit()
    changes = client.get("/api/v1/stocks/HSTX/financial-changes")
    assert changes.status_code == 200
    body = changes.json()
    assert body["vs_previous"]["revenue_change"] == pytest.approx(0.2)
    assert body["vs_year_ago"]["profit_change"] == pytest.approx(2 / 3)
    history = client.get("/api/v1/stocks/HSTX/history").json()
    assert len(history["quotes"]) == 2 and {row["last"] for row in history["quotes"]} == {100, 105}


def test_portfolio_mixes_stock_and_bond_without_crossing_formulas(session, seeded):
    from sqlalchemy import select
    from app.models.bond import Bond
    from app.models.portfolio import Portfolio, PortfolioPosition
    from app.services.portfolio_service import PortfolioService
    stock = _seed_stock(session, "MIXS")
    bond = session.scalar(select(Bond).limit(1))
    portfolio = Portfolio(anonymous_token="mixed-test", name="Mixed", base_currency="KZT")
    session.add(portfolio); session.flush()
    session.add_all([
        PortfolioPosition(portfolio_id=portfolio.id, bond_id=bond.id, stock_id=None, instrument_type="bond", quantity=2, purchase_clean_price=100),
        PortfolioPosition(portfolio_id=portfolio.id, bond_id=None, stock_id=stock.id, instrument_type="stock", quantity=10, purchase_price=90),
    ])
    session.flush(); session.expire_all()
    result = PortfolioService(session).valuation(PortfolioService(session).require(portfolio.id))
    assert {row["instrument_type"] for row in result["positions"]} == {"bond", "stock"}
    stock_row = next(row for row in result["positions"] if row["instrument_type"] == "stock")
    assert stock_row["ytm"] is None and stock_row["modified_duration"] is None
    assert result["summary"]["asset_allocation"]["stocks"] > 0
    assert result["summary"]["currency_allocation"]["KZT"] > 0
    assert result["summary"]["issuer_concentration"][0]["issuer_name"]
