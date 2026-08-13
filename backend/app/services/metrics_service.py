"""Derive BondMetric rows from reference data plus the latest quote.

Everything here runs through ``app.calculations``. No LLM, no guessing: when an
input is missing the derived field stays NULL.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.calculations.bond_math import (
    calculate_accrued_interest,
    calculate_bid_ask_spread,
    calculate_convexity,
    calculate_current_yield,
    calculate_duration,
    calculate_modified_duration,
    calculate_pull_to_par,
    calculate_ytm,
)
from app.calculations.cashflows import calculate_cashflows
from app.calculations.daycount import year_fraction
from app.calculations.returns import calculate_real_return
from app.calculations.types import FORMULA_VERSION, BondSpec, CouponPeriod
from app.core.logging import get_logger
from app.models.bond import Bond
from app.models.market import BondQuote
from app.models.metrics import BondMetric
from app.providers.inflation import InflationReading, get_inflation
from app.repositories.bonds import CashFlowRepository
from app.repositories.market import QuoteRepository, TradeRepository
from app.repositories.metrics import MetricRepository
from app.services.curve_service import get_risk_free_rate

logger = get_logger(__name__)


def bond_to_spec(bond: Bond) -> BondSpec | None:
    """Build the pricing spec, including the stored coupon schedule.

    When KASE's published schedule has been synced, it travels with the spec
    and the engine prices off real payment dates and per-period rates instead
    of rolling a schedule backwards from maturity.
    """
    if bond.maturity_date is None:
        return None

    nominal = bond.nominal or 100.0
    schedule: tuple[CouponPeriod, ...] = ()
    stored = getattr(bond, "cashflows", None) or ()
    if stored:
        periods = []
        for row in sorted(stored, key=lambda r: r.payment_date):
            # Recover the annual rate this period was written with, so a
            # floating issue keeps its per-period fixings.
            rate = None
            if row.coupon_amount is not None and nominal:
                frequency = bond.coupon_frequency or 1
                rate = row.coupon_amount * frequency / nominal
            periods.append(
                CouponPeriod(
                    payment_date=row.payment_date,
                    rate=rate,
                    period_start=row.period_start,
                )
            )
        schedule = tuple(periods)

    return BondSpec(
        maturity_date=bond.maturity_date,
        coupon_rate=bond.coupon_rate,
        coupon_frequency=bond.coupon_frequency,
        nominal=nominal,
        issue_date=bond.issue_date,
        next_coupon_date=bond.next_coupon_date,
        coupon_type=bond.coupon_type or "fixed",
        day_count=bond.day_count or "ACT/365F",
        schedule=schedule,
    )


def price_to_money(price_pct: float | None, nominal: float | None) -> float | None:
    """Convert a percent-of-nominal price into money terms."""
    if price_pct is None or nominal is None:
        return None
    return price_pct / 100.0 * nominal


class MetricsService:
    def __init__(self, session: Session):
        self.session = session
        self.quotes = QuoteRepository(session)
        self.trades = TradeRepository(session)
        self.metrics = MetricRepository(session)
        self.cashflows = CashFlowRepository(session)

    # -- cash flows -------------------------------------------------------

    def rebuild_cashflows(self, bond: Bond, settlement: date | None = None) -> int:
        """Regenerate the projected schedule.

        A schedule published by the exchange is never overwritten by a
        projected one: real payment dates and per-period rates outrank
        anything this can compute, and losing them would silently downgrade
        every figure derived from them.
        """
        existing = self.cashflows.for_bond(bond.id)
        if any(row.source and row.source != "calculated" for row in existing):
            return len(existing)

        spec = bond_to_spec(bond)
        if spec is None:
            return 0
        settlement = settlement or date.today()
        flows = calculate_cashflows(spec, settlement)
        self.cashflows.replace(
            bond.id,
            [
                {
                    "payment_date": f.payment_date,
                    "period_start": f.period_start,
                    "coupon_amount": f.coupon_amount,
                    "principal_amount": f.principal_amount,
                    "total_amount": f.total_amount,
                    "is_estimated": f.is_estimated,
                    "is_final": f.is_final,
                    "source": "calculated",
                    "source_identifier": bond.ticker,
                }
                for f in flows
            ],
        )
        return len(flows)

    # -- metrics ----------------------------------------------------------

    def compute(
        self,
        bond: Bond,
        *,
        quote: BondQuote | None = None,
        settlement: date | None = None,
        inflation_source: str = "automatic",
        manual_inflation_rate: float | None = None,
        persist: bool = True,
    ) -> BondMetric | None:
        spec = bond_to_spec(bond)
        if spec is None:
            logger.info("bond %s has no maturity date; metrics skipped", bond.ticker)
            return None

        settlement = settlement or date.today()
        quote = quote or self.quotes.latest(bond.id)
        now = datetime.now(timezone.utc)

        years_to_maturity = year_fraction(settlement, bond.maturity_date, spec.day_count)
        if years_to_maturity <= 0:
            return None

        flows = calculate_cashflows(spec, settlement)
        accrued_money = calculate_accrued_interest(spec, settlement)
        nominal = spec.nominal
        accrued_pct = None if accrued_money is None else accrued_money / nominal * 100.0

        clean_price = quote.clean_price if quote else None
        if clean_price is None and quote is not None:
            clean_price = quote.last if quote.last is not None else None
        dirty_price = None
        if clean_price is not None and accrued_pct is not None:
            dirty_price = clean_price + accrued_pct
        elif quote is not None and quote.dirty_price is not None:
            dirty_price = quote.dirty_price

        frequency = spec.effective_frequency or 1

        # Prefer the exchange's own YTM; fall back to solving it ourselves.
        ytm = quote.ytm if quote else None
        ytm_source = "market" if ytm is not None else None
        if ytm is None and dirty_price is not None and flows:
            dirty_money = dirty_price / 100.0 * nominal
            ytm = calculate_ytm(
                flows, dirty_money, settlement, frequency=frequency, day_count=spec.day_count
            )
            ytm_source = "calculated" if ytm is not None else None

        macaulay = calculate_duration(
            flows, ytm, settlement, frequency=frequency, day_count=spec.day_count
        )
        modified = calculate_modified_duration(macaulay, ytm, frequency)
        convexity = calculate_convexity(
            flows, ytm, settlement, frequency=frequency, day_count=spec.day_count
        )

        risk_free = get_risk_free_rate(
            self.session, years_to_maturity, currency=bond.currency
        )
        credit_spread = None if (ytm is None or risk_free is None) else ytm - risk_free

        spread = calculate_bid_ask_spread(
            quote.bid if quote else None, quote.ask if quote else None
        )
        current_yield = calculate_current_yield(clean_price, bond.coupon_rate, 100.0)
        pull_to_par = calculate_pull_to_par(clean_price, years_to_maturity, 100.0)

        inflation: InflationReading | None = get_inflation(
            self.session,
            source=inflation_source,
            manual_rate=manual_inflation_rate,
            horizon_years=years_to_maturity,
        )
        real_ytm = (
            None
            if inflation is None
            else calculate_real_return(ytm, inflation.annual_rate)
        )

        liquidity = self.trades.liquidity_stats(bond.id, days=30)

        metric = BondMetric(
            bond_id=bond.id,
            quote_id=quote.id if quote else None,
            as_of=now,
            clean_price=clean_price,
            dirty_price=dirty_price,
            accrued_interest=accrued_pct,
            current_yield=current_yield,
            ytm=ytm,
            ytm_source=ytm_source,
            macaulay_duration=macaulay,
            modified_duration=modified,
            convexity=convexity,
            credit_spread=credit_spread,
            risk_free_rate=risk_free,
            bid_ask_spread=None if spread is None else spread["absolute"],
            bid_ask_spread_pct=None if spread is None else spread["pct"],
            pull_to_par=None if pull_to_par is None else pull_to_par["annualized"],
            years_to_maturity=years_to_maturity,
            real_ytm=real_ytm,
            inflation_rate_used=None if inflation is None else inflation.annual_rate,
            inflation_source_used=None if inflation is None else inflation.kind,
            avg_daily_turnover_30d=liquidity["avg_daily_turnover"],
            trading_days_30d=liquidity["trading_days"],
            price_volatility_90d=self._price_volatility(bond.id),
            data_mode=quote.data_mode if quote else None,
            formula_version=FORMULA_VERSION,
            model_version=FORMULA_VERSION,
            calculated_at=now,
        )
        if persist:
            self.metrics.add(metric)
        return metric

    def _price_volatility(self, bond_id: int, days: int = 90) -> float | None:
        """Standard deviation of daily clean-price returns over the window."""
        history = [
            q.clean_price
            for q in self.quotes.history(bond_id, days=days)
            if q.clean_price is not None and q.clean_price > 0
        ]
        if len(history) < 5:
            return None
        returns = [
            history[i] / history[i - 1] - 1.0 for i in range(1, len(history))
        ]
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return variance**0.5
