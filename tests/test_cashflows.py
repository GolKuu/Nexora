from __future__ import annotations

from datetime import date

import pytest

from app.calculations.cashflows import (
    calculate_cashflows,
    next_coupon_date,
    previous_coupon_date,
)
from app.calculations.daycount import add_months, year_fraction
from app.calculations.types import BondSpec


def semiannual(maturity: date = date(2028, 1, 15)) -> BondSpec:
    return BondSpec(
        maturity_date=maturity,
        coupon_rate=0.14,
        coupon_frequency=2,
        nominal=1000.0,
        issue_date=date(2023, 1, 15),
    )


def test_schedule_has_one_payment_per_period():
    spec = semiannual()
    flows = calculate_cashflows(spec, date(2025, 1, 15))
    # 2025-07-15 through 2028-01-15 inclusive == 6 payments.
    assert len(flows) == 6
    assert flows[0].payment_date == date(2025, 7, 15)
    assert flows[-1].payment_date == spec.maturity_date


def test_only_the_final_flow_repays_principal():
    flows = calculate_cashflows(semiannual(), date(2025, 1, 15))
    assert all(f.principal_amount == 0 for f in flows[:-1])
    assert flows[-1].principal_amount == pytest.approx(1000.0)
    assert sum(1 for f in flows if f.is_final) == 1


def test_coupon_amount_is_annual_rate_divided_by_frequency():
    flows = calculate_cashflows(semiannual(), date(2025, 1, 15))
    assert flows[0].coupon_amount == pytest.approx(1000 * 0.14 / 2)


def test_matured_bond_has_no_cashflows():
    spec = semiannual(maturity=date(2024, 1, 15))
    assert calculate_cashflows(spec, date(2025, 1, 15)) == []


def test_zero_coupon_has_a_single_flow():
    spec = BondSpec(
        maturity_date=date(2027, 6, 1),
        coupon_rate=None,
        coupon_frequency=None,
        nominal=1000.0,
        coupon_type="zero",
    )
    flows = calculate_cashflows(spec, date(2025, 1, 1))
    assert len(flows) == 1
    assert flows[0].coupon_amount == 0.0
    assert flows[0].total_amount == pytest.approx(1000.0)


def test_floating_coupons_are_flagged_as_estimated():
    spec = BondSpec(
        maturity_date=date(2028, 1, 15),
        coupon_rate=0.16,
        coupon_frequency=4,
        nominal=1000.0,
        coupon_type="floating",
    )
    flows = calculate_cashflows(spec, date(2025, 1, 15))
    assert flows and all(f.is_estimated for f in flows)


def test_next_and_previous_coupon_dates_bracket_settlement():
    spec = semiannual()
    settlement = date(2025, 4, 1)
    nxt = next_coupon_date(spec, settlement)
    prev = previous_coupon_date(spec, settlement)
    assert prev < settlement < nxt
    assert nxt == date(2025, 7, 15)
    assert prev == date(2025, 1, 15)


def test_invalid_frequency_is_rejected():
    spec = BondSpec(
        maturity_date=date(2028, 1, 1),
        coupon_rate=0.10,
        coupon_frequency=3,
        nominal=100.0,
    )
    with pytest.raises(ValueError):
        calculate_cashflows(spec, date(2025, 1, 1))


# -- day count --------------------------------------------------------------

def test_add_months_clamps_to_month_end():
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 3, 15), -3) == date(2024, 12, 15)


def test_year_fraction_conventions():
    start, end = date(2025, 1, 1), date(2025, 7, 1)
    assert year_fraction(start, end, "ACT/365F") == pytest.approx(181 / 365)
    assert year_fraction(start, end, "ACT/360") == pytest.approx(181 / 360)
    assert year_fraction(start, end, "30/360") == pytest.approx(0.5)
    assert year_fraction(end, start, "ACT/365F") == pytest.approx(-181 / 365)


def test_act_act_accounts_for_leap_years():
    assert year_fraction(date(2024, 1, 1), date(2025, 1, 1), "ACT/ACT") == pytest.approx(1.0)
    assert year_fraction(date(2025, 1, 1), date(2026, 1, 1), "ACT/ACT") == pytest.approx(1.0)
