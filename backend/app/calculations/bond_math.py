"""Core bond pricing and risk measures.

Prices are percentages of nominal (par == 100) whenever the nominal passed in
is the default 100; otherwise they are money amounts in the bond's currency.
Rates are decimals: 0.145 means 14.5 %.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.calculations.cashflows import calculate_cashflows, previous_coupon_date
from app.calculations.daycount import year_fraction
from app.calculations.types import BondSpec, CashFlow

_YTM_LOWER = -0.95
_YTM_UPPER = 10.0
_TOLERANCE = 1e-10
_MAX_ITERATIONS = 200


def calculate_accrued_interest(
    spec: BondSpec, settlement: date
) -> float | None:
    """Interest earned by the seller between the last coupon and settlement."""
    if spec.is_zero_coupon:
        return 0.0
    if spec.coupon_rate is None:
        return None
    previous = previous_coupon_date(spec, settlement)
    if previous is None:
        return None
    if settlement <= previous:
        return 0.0
    fraction = year_fraction(previous, settlement, spec.day_count)
    return spec.nominal * spec.coupon_rate * fraction


def calculate_current_yield(
    clean_price: float | None,
    coupon_rate: float | None,
    nominal: float = 100.0,
) -> float | None:
    """Annual coupon income divided by the price actually paid for it."""
    if clean_price is None or coupon_rate is None:
        return None
    if clean_price <= 0:
        return None
    return (nominal * coupon_rate) / clean_price


def _discount_factor(rate: float, frequency: int, years: float) -> float:
    base = 1.0 + rate / frequency
    if base <= 0:
        # Deep-negative yields are not economically meaningful here.
        return float("inf")
    return base ** (-frequency * years)


def calculate_bond_price(
    cashflows: Sequence[CashFlow],
    ytm: float | None,
    settlement: date,
    *,
    frequency: int = 2,
    day_count: str = "ACT/365F",
    accrued_interest: float | None = None,
) -> dict[str, float] | None:
    """Present value of a cash-flow stream discounted at ``ytm``.

    Returns dirty price, and clean price when accrued interest is known.
    """
    if ytm is None or not cashflows:
        return None
    frequency = max(1, frequency)
    dirty = 0.0
    for flow in cashflows:
        years = year_fraction(settlement, flow.payment_date, day_count)
        if years < 0:
            continue
        dirty += flow.total_amount * _discount_factor(ytm, frequency, years)
    result = {"dirty_price": dirty}
    if accrued_interest is not None:
        result["accrued_interest"] = accrued_interest
        result["clean_price"] = dirty - accrued_interest
    return result


def price_from_spec(
    spec: BondSpec, ytm: float, settlement: date
) -> dict[str, float] | None:
    """Convenience wrapper: build the schedule, accrue, then discount."""
    flows = calculate_cashflows(spec, settlement)
    if not flows:
        return None
    accrued = calculate_accrued_interest(spec, settlement)
    return calculate_bond_price(
        flows,
        ytm,
        settlement,
        frequency=spec.coupon_frequency or 1,
        day_count=spec.day_count,
        accrued_interest=accrued,
    )


def calculate_ytm(
    cashflows: Sequence[CashFlow],
    dirty_price: float | None,
    settlement: date,
    *,
    frequency: int = 2,
    day_count: str = "ACT/365F",
) -> float | None:
    """Solve for the yield that reproduces ``dirty_price``.

    Bisection on a strictly monotonic function - slower than Newton but it
    cannot diverge, which matters more than speed for a batch job.
    """
    if dirty_price is None or dirty_price <= 0 or not cashflows:
        return None
    frequency = max(1, frequency)

    def pv(rate: float) -> float:
        total = 0.0
        for flow in cashflows:
            years = year_fraction(settlement, flow.payment_date, day_count)
            if years < 0:
                continue
            total += flow.total_amount * _discount_factor(rate, frequency, years)
        return total

    low, high = _YTM_LOWER, _YTM_UPPER
    pv_low, pv_high = pv(low), pv(high)
    if pv_low < dirty_price or pv_high > dirty_price:
        # The price lies outside the solvable range; refuse rather than guess.
        return None

    for _ in range(_MAX_ITERATIONS):
        mid = (low + high) / 2
        value = pv(mid)
        if abs(value - dirty_price) < _TOLERANCE:
            return mid
        if value > dirty_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def calculate_duration(
    cashflows: Sequence[CashFlow],
    ytm: float | None,
    settlement: date,
    *,
    frequency: int = 2,
    day_count: str = "ACT/365F",
) -> float | None:
    """Macaulay duration in years - the PV-weighted average time to payment."""
    if ytm is None or not cashflows:
        return None
    frequency = max(1, frequency)
    total_pv = 0.0
    weighted = 0.0
    for flow in cashflows:
        years = year_fraction(settlement, flow.payment_date, day_count)
        if years < 0:
            continue
        pv = flow.total_amount * _discount_factor(ytm, frequency, years)
        total_pv += pv
        weighted += pv * years
    if total_pv <= 0:
        return None
    return weighted / total_pv


def calculate_modified_duration(
    macaulay_duration: float | None,
    ytm: float | None,
    frequency: int = 2,
) -> float | None:
    """Percentage price change per 1.00 (100 bp) change in yield."""
    if macaulay_duration is None or ytm is None:
        return None
    frequency = max(1, frequency)
    denominator = 1.0 + ytm / frequency
    if denominator <= 0:
        return None
    return macaulay_duration / denominator


def calculate_convexity(
    cashflows: Sequence[CashFlow],
    ytm: float | None,
    settlement: date,
    *,
    frequency: int = 2,
    day_count: str = "ACT/365F",
) -> float | None:
    """Second-order price sensitivity, in years squared."""
    if ytm is None or not cashflows:
        return None
    frequency = max(1, frequency)
    base = 1.0 + ytm / frequency
    if base <= 0:
        return None
    total_pv = 0.0
    weighted = 0.0
    for flow in cashflows:
        years = year_fraction(settlement, flow.payment_date, day_count)
        if years < 0:
            continue
        pv = flow.total_amount * _discount_factor(ytm, frequency, years)
        total_pv += pv
        weighted += pv * years * (years + 1.0 / frequency)
    if total_pv <= 0:
        return None
    return weighted / (total_pv * base**2)


def calculate_credit_spread(
    ytm: float | None, risk_free_rate: float | None
) -> float | None:
    """Yield pick-up over the government curve at the same tenor."""
    if ytm is None or risk_free_rate is None:
        return None
    return ytm - risk_free_rate


def calculate_bid_ask_spread(
    bid: float | None, ask: float | None
) -> dict[str, float] | None:
    """Absolute and relative width of the order book."""
    if bid is None or ask is None:
        return None
    if bid <= 0 or ask <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2
    if mid <= 0:
        return None
    absolute = ask - bid
    return {
        "absolute": absolute,
        "mid": mid,
        "pct": absolute / mid,
    }


def calculate_pull_to_par(
    clean_price: float | None,
    years_to_maturity: float | None,
    nominal: float = 100.0,
) -> dict[str, float] | None:
    """Return that comes purely from the price converging to par at redemption.

    ``total`` is the whole gain over the remaining life, ``annualized`` is the
    same gain expressed per year.
    """
    if clean_price is None or years_to_maturity is None:
        return None
    if clean_price <= 0 or years_to_maturity <= 0:
        return None
    total = nominal / clean_price - 1.0
    annualized = (nominal / clean_price) ** (1.0 / years_to_maturity) - 1.0
    return {"total": total, "annualized": annualized}
