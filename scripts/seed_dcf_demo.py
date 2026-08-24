"""Seed one clearly labelled, synthetic eligible equity for the local DCF flow."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.models  # noqa: F401,E402
from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.models.financials import FinancialStatement  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.issuer import Issuer  # noqa: E402
from app.models.macro import InflationData, YieldCurve  # noqa: E402
from app.models.stock import Stock, StockQuote  # noqa: E402


def main() -> None:
    if settings.is_production:
        raise SystemExit("Refusing to seed synthetic DCF data in production")
    Base.metadata.create_all(engine)
    session = SessionLocal(); now = datetime.now(timezone.utc)
    try:
        issuer = session.query(Issuer).filter_by(code="DCFDEMO-ISS").one_or_none()
        if issuer is None:
            issuer = Issuer(code="DCFDEMO-ISS", name="Nexora Industrial Demo JSC", short_name="Nexora Industrial",
                country="KZ", sector="corporate", industry="Industrial technology", is_financial_institution=False,
                description="SYNTHETIC DEMO DATA for deterministic DCF validation only.", source="mock")
            session.add(issuer); session.flush()
            instrument = Instrument(ticker="DCFDEMO", issuer_id=issuer.id, instrument_type="stock", currency="KZT",
                is_active=True, security_type="common stock", source="mock")
            session.add(instrument); session.flush()
            stock = Stock(instrument_id=instrument.id, shares_outstanding=10_000_000, sector="Industrials", industry="Technology",
                lot_size=1, liquidity_class=1, source="mock")
            session.add(stock); session.flush()
            for year, revenue in ((2023,80e9),(2024,90e9),(2025,100e9)):
                session.add(FinancialStatement(issuer_id=issuer.id, period_end=date(year,12,31), period_type="FY", fiscal_year=year,
                    currency="KZT", is_audited=True, is_consolidated=True, standard="IFRS", revenue=revenue,
                    operating_profit=revenue*.16, ebitda=revenue*.20, net_profit=revenue*.10, interest_expense=1.2e9,
                    total_assets=160e9, total_equity=95e9, total_debt=20e9, cash_and_equivalents=5e9,
                    current_assets=30e9, current_liabilities=20e9, capex=revenue*.06, operating_cash_flow=revenue*.14,
                    source="mock", source_identifier=f"dcf-demo-{year}", source_timestamp=now, fetched_at=now))
            session.add(StockQuote(stock_id=stock.id, timestamp=now, last=5_000, close=5_000, bid=4_990, ask=5_010,
                volume=100_000, data_mode="mock", source="mock", source_timestamp=now, fetched_at=now))
        if not session.query(YieldCurve).filter_by(currency="KZT", tenor_years=5.0).first():
            session.add(YieldCurve(curve_code="KZ_GOV", currency="KZT", as_of_date=date.today(), tenor_years=5,
                yield_rate=.10, source="mock", source_timestamp=now, fetched_at=now))
        if not session.query(InflationData).filter_by(country="KZ", kind="forecast", horizon_years=10.0).first():
            session.add(InflationData(country="KZ", period_end=date.today(), kind="forecast", annual_rate=.055,
                horizon_years=10, source="mock", source_timestamp=now, fetched_at=now))
        session.commit(); print("DCFDEMO ready (synthetic local data)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
