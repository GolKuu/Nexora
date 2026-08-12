"""Coupon schedule generation."""

from __future__ import annotations

from datetime import date

from app.calculations.daycount import add_months, months_per_period
from app.calculations.types import BondSpec, CashFlow


def _schedule_dates(spec: BondSpec, after: date) -> list[date]:
    """Payment dates strictly after ``after``, ending on the maturity date.

    The schedule is rolled backwards from maturity, which is what issuers do and
    what keeps the final period exact.
    """
    step = months_per_period(spec.coupon_frequency or 1)
    dates: list[date] = []
    cursor = spec.maturity_date
    # A hard bound keeps a malformed spec from looping forever.
    max_periods = 12 * 100
    lower_bound = spec.issue_date or after
    for _ in range(max_periods):
        if cursor <= after or cursor <= lower_bound:
            break
        dates.append(cursor)
        cursor = add_months(spec.maturity_date, -step * len(dates))
    return sorted(dates)


def previous_coupon_date(spec: BondSpec, settlement: date) -> date | None:
    """The accrual start date for the coupon period containing ``settlement``."""
    if spec.is_zero_coupon:
        return spec.issue_date
    step = months_per_period(spec.coupon_frequency or 1)
    upcoming = next_coupon_date(spec, settlement)
    if upcoming is None:
        return None
    previous = add_months(upcoming, -step)
    if spec.issue_date and previous < spec.issue_date:
        return spec.issue_date
    return previous


def next_coupon_date(spec: BondSpec, settlement: date) -> date | None:
    if spec.is_zero_coupon:
        return spec.maturity_date if spec.maturity_date > settlement else None
    if spec.next_coupon_date and spec.next_coupon_date > settlement:
        return spec.next_coupon_date
    upcoming = [d for d in _schedule_dates(spec, settlement) if d > settlement]
    return upcoming[0] if upcoming else None


def calculate_cashflows(spec: BondSpec, settlement: date) -> list[CashFlow]:
    """All payments a buyer settling on ``settlement`` is entitled to.

    Returns an empty list for a matured bond. A floating-rate bond is projected
    at its current coupon rate and every flow is flagged ``is_estimated``.
    """
    problems = spec.validate()
    if problems:
        raise ValueError("; ".join(problems))
    if spec.maturity_date <= settlement:
        return []

    nominal = spec.nominal
    estimated = spec.coupon_type in ("floating", "indexed", "step")

    if spec.is_zero_coupon:
        return [
            CashFlow(
                payment_date=spec.maturity_date,
                coupon_amount=0.0,
                principal_amount=nominal,
                period_start=spec.issue_date,
                is_estimated=estimated,
            )
        ]

    frequency = spec.coupon_frequency or 1
    coupon = nominal * (spec.coupon_rate or 0.0) / frequency
    step = months_per_period(frequency)

    flows: list[CashFlow] = []
    for payment_date in _schedule_dates(spec, settlement):
        flows.append(
            CashFlow(
                payment_date=payment_date,
                coupon_amount=coupon,
                principal_amount=nominal if payment_date == spec.maturity_date else 0.0,
                period_start=add_months(payment_date, -step),
                is_estimated=estimated,
            )
        )
    if not flows or flows[-1].payment_date != spec.maturity_date:
        flows.append(
            CashFlow(
                payment_date=spec.maturity_date,
                coupon_amount=coupon,
                principal_amount=nominal,
                period_start=add_months(spec.maturity_date, -step),
                is_estimated=estimated,
            )
        )
    return flows
