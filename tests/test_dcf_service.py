from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.api.deps import Identity
from app.core.errors import InsufficientDataError, ValidationError
from app.models.dcf import DCFRun, DCFUsageEvent
from app.models.financials import FinancialStatement
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.macro import InflationData, YieldCurve
from app.models.stock import Stock, StockQuote
from app.services.dcf_service import DCFService


def _eligible(session, ticker: str = "DCFTEST") -> Stock:
    now = datetime.now(timezone.utc)
    issuer = Issuer(code=f"{ticker}-ISS", name="Stable Industrial", country="KZ", sector="corporate", is_financial_institution=False)
    session.add(issuer); session.flush()
    instrument = Instrument(ticker=ticker, issuer_id=issuer.id, instrument_type="stock", currency="KZT", is_active=True)
    session.add(instrument); session.flush()
    stock = Stock(instrument_id=instrument.id, shares_outstanding=10_000_000, sector="industrial")
    session.add(stock); session.flush()
    for year, revenue in ((2023, 80e9), (2024, 90e9), (2025, 100e9)):
        session.add(FinancialStatement(issuer_id=issuer.id, period_end=date(year,12,31), period_type="FY", fiscal_year=year,
            currency="KZT", is_audited=True, is_consolidated=True, standard="IFRS", revenue=revenue,
            operating_profit=revenue*.16, ebitda=revenue*.20, net_profit=revenue*.10,
            interest_expense=1.2e9, total_debt=20e9, cash_and_equivalents=5e9,
            current_assets=30e9, current_liabilities=20e9, capex=revenue*.06,
            operating_cash_flow=revenue*.14, source="golden-fixture", source_timestamp=now, fetched_at=now))
    session.add(StockQuote(stock_id=stock.id, timestamp=now, last=5_000, close=5_000,
        data_mode="mock", source="golden-fixture", source_timestamp=now, fetched_at=now))
    session.add(YieldCurve(curve_code="KZ_GOV", currency="KZT", as_of_date=date.today(), tenor_years=5,
        yield_rate=.10, source="golden-fixture", source_timestamp=now, fetched_at=now))
    session.add(InflationData(country="KZ", period_end=date.today(), kind="forecast", annual_rate=.055,
        horizon_years=10, source="golden-fixture", source_timestamp=now, fetched_at=now))
    session.commit()
    return stock


def test_end_to_end_persists_audit_and_cache_does_not_consume_quota(session) -> None:
    stock = _eligible(session)
    identity = Identity(user_id=None, token="dcf-test-owner")
    first = DCFService(session).analyze(stock.instrument.ticker, identity)
    assert first["status"] == "completed"
    assert first["scenarios"]["bear"]["fair_value"] <= first["scenarios"]["base"]["fair_value"] <= first["scenarios"]["bull"]["fair_value"]
    history = first["financial_changes_2y"]
    assert history["requested_years"] == 2
    assert history["status"] == "complete" and len(history["periods"]) == 2
    assert [row["period_end"] for row in history["periods"]] == ["2024-12-31", "2025-12-31"]
    assert history["changes"]["revenue_change"] == pytest.approx(1 / 9)
    assert history["changes"]["ebit_margin_change"] == pytest.approx(0)
    assert "not an individual investment recommendation" in first["disclaimer"]
    run = session.get(DCFRun, first["run_id"])
    assert run.dcf_model_version == "corporate-fcff-1.0.0"
    assert run.prompt_version == "dcf-explanation-rules-1.0"
    assert len(run.scenarios) == 3 and len(run.snapshots) == 4
    assert len(run.assumptions) == 24
    session.add(StockQuote(stock_id=stock.id, timestamp=datetime.now(timezone.utc)+timedelta(seconds=1), last=6_000, close=6_000,
        data_mode="mock", source="new-market-tick")); session.commit()
    second = DCFService(session).analyze(stock.instrument.ticker, identity)
    assert second["run_id"] == first["run_id"] and second["cache_hit"] is True
    assert second["current_price"] == 6_000
    assert second["scenarios"]["base"]["fair_value"] == first["scenarios"]["base"]["fair_value"]
    assert second["scenarios"]["base"]["difference_percent"] != first["scenarios"]["base"]["difference_percent"]
    counted = session.query(DCFUsageEvent).filter_by(anonymous_token_hash=run.anonymous_token_hash, counted=True).count()
    assert counted == 1


def test_insufficient_data_never_returns_target(session) -> None:
    issuer = Issuer(code="NO-DATA", name="No Data", country="KZ", sector="corporate", is_financial_institution=False)
    session.add(issuer); session.flush()
    instrument = Instrument(ticker="NODCF", issuer_id=issuer.id, instrument_type="stock", currency="KZT", is_active=True)
    session.add(instrument); session.flush(); session.add(Stock(instrument_id=instrument.id, shares_outstanding=10)); session.commit()
    with pytest.raises(InsufficientDataError, match="quality gate") as error:
        DCFService(session).analyze("NODCF", Identity(None, "no-data-owner"))
    assert error.value.details["readiness"] == "NOT_READY"


def test_bank_routes_to_unsupported_model(session) -> None:
    issuer = Issuer(code="BANK-DCF", name="Bank", country="KZ", sector="bank", is_financial_institution=True)
    session.add(issuer); session.flush()
    instrument = Instrument(ticker="BANKDCF", issuer_id=issuer.id, instrument_type="stock", currency="KZT", is_active=True)
    session.add(instrument); session.flush(); session.add(Stock(instrument_id=instrument.id, shares_outstanding=10)); session.commit()
    with pytest.raises(ValidationError, match="not currently available"):
        DCFService(session).analyze("BANKDCF", Identity(None, "bank-owner"))


def test_retail_result_api_and_admin_audit_are_separated(session, api) -> None:
    stock = _eligible(session, "DCFAPI")
    headers = {"X-Anon-Token": "dcf-api-owner"}
    response = api.post(f"/stocks/{stock.instrument.ticker}/dcf", json={"force_refresh": False}, headers=headers)
    assert response.status_code == 200
    retail = response.json()
    assert "input_snapshots" not in retail and "assumptions" not in retail
    reopened = api.get(f"/dcf/{retail['run_id']}", headers=headers)
    assert reopened.status_code == 200 and reopened.json()["run_id"] == retail["run_id"]
    audit = api.get(f"/admin/dcf/{retail['run_id']}")
    assert audit.status_code == 200
    assert len(audit.json()["assumptions"]) == 24
    usage = api.get("/me/dcf-usage", headers=headers).json()
    assert usage["used"] == 1 and usage["remaining"] == 2


def test_new_available_report_invalidates_old_run(session) -> None:
    stock = _eligible(session, "DCFNEW")
    identity = Identity(None, "new-report-owner")
    first = DCFService(session).analyze("DCFNEW", identity)
    issuer_id = stock.instrument.issuer_id; now = datetime.now(timezone.utc)
    session.add(FinancialStatement(issuer_id=issuer_id, period_end=date(2026,6,30), period_type="FY", fiscal_year=2026,
        currency="KZT", is_audited=True, is_consolidated=True, standard="IFRS", revenue=110e9,
        operating_profit=18e9, ebitda=22e9, net_profit=11e9, interest_expense=1.1e9,
        total_debt=18e9, cash_and_equivalents=7e9, current_assets=32e9, current_liabilities=19e9,
        capex=6e9, operating_cash_flow=16e9, source="new-report", source_timestamp=now, fetched_at=now))
    session.commit()
    second = DCFService(session).analyze("DCFNEW", identity)
    assert second["run_id"] != first["run_id"]
    assert session.get(DCFRun, first["run_id"]).stale_due_to_new_financials is True
