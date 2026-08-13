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
    step = months_per_period(spec.effective_frequency or 1)
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
    if spec.schedule:
        past = [p.payment_date for p in spec.schedule if p.payment_date <= settlement]
        if past:
            return max(past)
    step = months_per_period(spec.effective_frequency or 1)
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
    # A published schedule outranks both the hint field and any projection.
    if spec.schedule:
        upcoming = [p.payment_date for p in spec.schedule if p.payment_date > settlement]
        if upcoming:
            return min(upcoming)
    if spec.next_coupon_date and spec.next_coupon_date > settlement:
        return spec.next_coupon_date
    upcoming = [d for d in _schedule_dates(spec, settlement) if d > settlement]
    return upcoming[0] if upcoming else None


def _flows_from_schedule(spec: BondSpec, settlement: date) -> list[CashFlow]:
    """Build flows from the issuer's published schedule.

    Each period carries the rate that actually applies to it, so a floating or
    indexed bond is priced off published fixings rather than an assumption
    that today's coupon lasts forever. Only periods whose rate is still
    unfixed fall back to the current rate, and only those are marked
    estimated.
    """
    nominal = spec.nominal
    upcoming = [p for p in spec.schedule if p.payment_date > settlement]
    if not upcoming:
        return []

    flows: list[CashFlow] = []
    previous_date = None
    for index, period in enumerate(upcoming):
        rate = period.rate
        estimated = rate is None
        if rate is None:
            rate = spec.coupon_rate or 0.0
        # The published schedule states an annual rate per period; the payment
        # covers one period of the issue's own frequency.
        frequency = spec.effective_frequency or 1
        coupon = nominal * rate / frequency
        period_start = period.period_start or previous_date
        if period_start is None and index == 0:
            period_start = previous_coupon_date(spec, settlement)
        is_final = period.payment_date >= spec.maturity_date
        flows.append(
            CashFlow(
                payment_date=period.payment_date,
                coupon_amount=coupon,
                principal_amount=nominal if is_final else 0.0,
                period_start=period_start,
                is_estimated=estimated,
            )
        )
        previous_date = period.payment_date

    # The schedule sometimes stops at the last coupon; redemption still happens.
    if flows and flows[-1].principal_amount == 0.0:
        last = flows[-1]
        if spec.maturity_date > last.payment_date:
            flows.append(
                CashFlow(
                    payment_date=spec.maturity_date,
                    coupon_amount=0.0,
                    principal_amount=nominal,
                    period_start=last.payment_date,
                    is_estimated=False,
                )
            )
        else:
            flows[-1] = CashFlow(
                payment_date=last.payment_date,
                coupon_amount=last.coupon_amount,
                principal_amount=nominal,
                period_start=last.period_start,
                is_estimated=last.is_estimated,
            )
    return flows



def calculate_cashflows(spec: BondSpec, settlement: date) -> list[CashFlow]:
    """All payments a buyer settling on ``settlement`` is entitled to.

    Returns an empty list for a matured bond. When the issuer publishes a
    schedule it is used verbatim; otherwise the schedule is rolled backwards
    from maturity and a floating-rate bond is projected at its current coupon
    with every flow flagged ``is_estimated``.
    """
    problems = spec.validate()
    if problems:
        raise ValueError("; ".join(problems))
    if spec.maturity_date <= settlement:
        return []

    if spec.schedule:
        flows = _flows_from_schedule(spec, settlement)
        if flows:
            return flows

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

    frequency = spec.effective_frequency or 1
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
