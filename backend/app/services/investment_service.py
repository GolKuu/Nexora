"""Database-facing wrapper around the investment calculator.

Keeps the arithmetic in :mod:`app.services.investment_calculator` pure and
testable: this layer only assembles a :class:`MarketSnapshot` from stored
quotes and metrics, resolves the inflation reading, and hands over.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.providers.inflation import InflationReading
from app.repositories.market import QuoteRepository, TradeRepository
from app.repositories.metrics import MetricRepository
from app.services.investment_calculator import (
    Commission,
    InvestmentRequest,
    MarketSnapshot,
    calculate_investment,
    derive_risk_measures,
)
from app.services.metrics_service import bond_to_spec


class InvestmentService:
    def __init__(self, session: Session):
        self.session = session
        self.quotes = QuoteRepository(session)
        self.trades = TradeRepository(session)
        self.metrics = MetricRepository(session)

    def snapshot(self, bond: Bond, settlement: date) -> MarketSnapshot:
        """Assemble the market picture for one bond from stored data."""
        quote = self.quotes.latest(bond.id)
        metric = self.metrics.latest(bond.id)
        stats = self.trades.liquidity_stats(bond.id)

        modified_duration = getattr(metric, "modified_duration", None) if metric else None
        convexity = getattr(metric, "convexity", None) if metric else None

        # Derive risk measures on the fly when the batch job has not run yet,
        # so a freshly synced bond is still fully answerable.
        if modified_duration is None and quote is not None:
            spec = bond_to_spec(bond)
            price_pct = quote.ask or quote.clean_price or quote.last
            if spec is not None and price_pct is not None:
                dirty_money = price_pct / 100.0 * spec.nominal
                derived = derive_risk_measures(spec, dirty_money, settlement)
                modified_duration = derived["modified_duration"]
                convexity = derived["convexity"]

        last_trade = stats.get("last_trade_date") if isinstance(stats, dict) else None
        if hasattr(last_trade, "date"):
            last_trade = last_trade.date()

        return MarketSnapshot(
            ask=getattr(quote, "ask", None),
            bid=getattr(quote, "bid", None),
            last=getattr(quote, "last", None) or getattr(quote, "clean_price", None),
            ytm=getattr(quote, "ytm", None),
            turnover=getattr(quote, "turnover", None),
            number_of_trades=getattr(quote, "number_of_trades", None),
            last_trade_date=last_trade,
            modified_duration=modified_duration,
            convexity=convexity,
            source=getattr(quote, "source", None),
            source_url=getattr(quote, "source_url", None),
            data_mode=getattr(quote, "data_mode", None),
            timestamp=getattr(quote, "source_timestamp", None)
            or getattr(quote, "timestamp", None),
        )

    def calculate(
        self,
        bond: Bond,
        *,
        amount: float,
        commission_type: str = "percent",
        commission_value: float = 0.0,
        inflation_enabled: bool = True,
        inflation: InflationReading | None = None,
        exit_mode: str = "maturity",
        exit_date: date | None = None,
        scenario: str = "base",
        settlement: date | None = None,
    ) -> dict:
        settlement = settlement or date.today()
        spec = bond_to_spec(bond)
        if spec is None:
            return {
                "bond_identifier": bond.ticker,
                "input_amount": amount,
                "quantity": 0,
                "warnings": [
                    "У выпуска не заполнена дата погашения: расчет невозможен."
                ],
                "cashflows": [],
            }

        request = InvestmentRequest(
            amount=amount,
            currency=bond.currency,
            commission=Commission(commission_type, commission_value),
            inflation_enabled=inflation_enabled,
            inflation_rate=inflation.annual_rate if inflation else None,
            inflation_source=_inflation_label(inflation),
            exit_mode=exit_mode,
            exit_date=exit_date,
            scenario=scenario,
            lot_size=getattr(bond, "lot_size", None) or 1.0,
            settlement=settlement,
        )
        return calculate_investment(
            spec,
            self.snapshot(bond, settlement),
            request,
            identifier=bond.ticker,
            currency=bond.currency,
        )


def _inflation_label(reading: InflationReading | None) -> str | None:
    if reading is None:
        return None
    parts = [reading.source]
    if reading.period_end:
        parts.append(reading.period_end.isoformat())
    return " · ".join(p for p in parts if p)
