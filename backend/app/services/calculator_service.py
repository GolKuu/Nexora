"""«Если вложить X ₸» - the one interaction the whole product is judged on.

Every number below comes from the calculation engine and the bond's own cash
flow schedule. Nothing is approximated silently: if the price is unknown, the
answer is "нет данных", not a plausible-looking figure.
"""

from __future__ import annotations

import math
from datetime import date

from sqlalchemy.orm import Session

from app.calculations.bond_math import calculate_accrued_interest
from app.calculations.cashflows import calculate_cashflows
from app.calculations.daycount import year_fraction
from app.calculations.returns import (
    calculate_annualized_return,
    calculate_real_total_return,
    calculate_total_return,
)
from app.models.bond import Bond
from app.repositories.metrics import MetricRepository
from app.services.metrics_service import bond_to_spec


class CalculatorService:
    def __init__(self, session: Session):
        self.session = session
        self.metrics = MetricRepository(session)

    def project(
        self,
        bond: Bond,
        amount: float,
        *,
        settlement: date | None = None,
        reinvest_coupons: bool = False,
    ) -> dict:
        settlement = settlement or date.today()
        metric = self.metrics.latest(bond.id)
        spec = bond_to_spec(bond)
        if spec is None or metric is None or metric.clean_price is None:
            return {
                "available": False,
                "reason": "Нет актуальной цены: расчет невозможен.",
            }

        nominal = spec.nominal
        clean_pct = metric.clean_price
        accrued_money = calculate_accrued_interest(spec, settlement) or 0.0
        price_per_bond = clean_pct / 100.0 * nominal + accrued_money
        if price_per_bond <= 0:
            return {"available": False, "reason": "Некорректная цена."}

        quantity = math.floor(amount / price_per_bond)
        if quantity <= 0:
            return {
                "available": False,
                "reason": (
                    f"Суммы недостаточно: одна облигация стоит примерно "
                    f"{price_per_bond:,.0f} {bond.currency}."
                ),
                "price_per_bond": round(price_per_bond, 2),
                "min_amount": round(price_per_bond, 2),
            }

        invested = quantity * price_per_bond
        change = amount - invested

        flows = calculate_cashflows(spec, settlement)
        schedule = []
        coupons_total = 0.0
        principal_total = 0.0
        for flow in flows:
            coupon = flow.coupon_amount * quantity
            principal = flow.principal_amount * quantity
            coupons_total += coupon
            principal_total += principal
            schedule.append(
                {
                    "date": flow.payment_date.isoformat(),
                    "coupon": round(coupon, 2),
                    "principal": round(principal, 2),
                    "total": round(coupon + principal, 2),
                    "is_estimated": flow.is_estimated,
                }
            )

        proceeds = coupons_total + principal_total
        years = year_fraction(settlement, bond.maturity_date, spec.day_count)
        total_return = calculate_total_return(invested, proceeds)
        annualized = calculate_annualized_return(total_return, years)
        real_total = calculate_real_total_return(
            total_return, metric.inflation_rate_used, years
        )
        real_annualized = calculate_annualized_return(real_total, years)

        profit = proceeds - invested
        real_profit = None
        if metric.inflation_rate_used is not None and years > 0:
            # Value of the same money at today's prices.
            deflator = (1.0 + metric.inflation_rate_used) ** years
            real_profit = proceeds / deflator - invested

        return {
            "available": True,
            "currency": bond.currency,
            "requested_amount": amount,
            "quantity": quantity,
            "price_per_bond": round(price_per_bond, 2),
            "accrued_interest_per_bond": round(accrued_money, 2),
            "invested": round(invested, 2),
            "uninvested_remainder": round(change, 2),
            "coupons_total": round(coupons_total, 2),
            "principal_total": round(principal_total, 2),
            "proceeds": round(proceeds, 2),
            "profit": round(profit, 2),
            "profit_real": None if real_profit is None else round(real_profit, 2),
            "years": round(years, 2),
            "total_return_pct": None if total_return is None else round(total_return * 100, 2),
            "annualized_return_pct": None if annualized is None else round(annualized * 100, 2),
            "real_total_return_pct": None if real_total is None else round(real_total * 100, 2),
            "real_annualized_return_pct": None
            if real_annualized is None
            else round(real_annualized * 100, 2),
            "inflation_pct": None
            if metric.inflation_rate_used is None
            else round(metric.inflation_rate_used * 100, 2),
            "inflation_source": metric.inflation_source_used,
            "reinvest_coupons": reinvest_coupons,
            "assumptions": [
                "Расчет по текущей рыночной цене и графику выплат выпуска.",
                "Купоны не реинвестируются." if not reinvest_coupons
                else "Купоны считаются реинвестированными под ту же доходность.",
                "Налоги и комиссии брокера не учитываются.",
                "Расчет предполагает, что эмитент выполнит все обязательства.",
            ],
            "schedule": schedule,
        }
