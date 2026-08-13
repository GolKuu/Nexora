"""Unit tests for the pricing core.

Where possible the expected value is derived from first principles inside the
test rather than copied from the implementation, so a wrong formula fails.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.calculations.bond_math import (
    calculate_accrued_interest,
    calculate_bid_ask_spread,
    calculate_bond_price,
    calculate_convexity,
    calculate_credit_spread,
    calculate_current_yield,
    calculate_duration,
    calculate_modified_duration,
    calculate_pull_to_par,
    calculate_ytc,
    calculate_ytm,
    calculate_ytp,
    price_from_spec,
)
from app.calculations.cashflows import calculate_cashflows
from app.calculations.types import BondSpec

SETTLEMENT = date(2025, 1, 1)


def annual_bond(years: int = 5, coupon: float = 0.10) -> BondSpec:
    return BondSpec(
        maturity_date=date(2025 + years, 1, 1),
        coupon_rate=coupon,
        coupon_frequency=1,
        nominal=100.0,
        issue_date=date(2025, 1, 1),
        day_count="ACT/365F",
    )


def zero_bond(years: int = 1) -> BondSpec:
    return BondSpec(
        maturity_date=date(2025 + years, 1, 1),
        coupon_rate=None,
        coupon_frequency=None,
        nominal=100.0,
        issue_date=date(2025, 1, 1),
        coupon_type="zero",
    )


# -- price -----------------------------------------------------------------

def test_price_at_par_when_yield_equals_coupon():
    spec = annual_bond(years=5, coupon=0.10)
    result = price_from_spec(spec, 0.10, SETTLEMENT)
    assert result is not None
    # ACT/365F over calendar years is not exactly 1.0 per period, so allow a
    # small drift, but the bond must price essentially at par.
    assert result["dirty_price"] == pytest.approx(100.0, abs=0.6)


def test_price_falls_when_yield_rises():
    spec = annual_bond()
    low = price_from_spec(spec, 0.08, SETTLEMENT)["dirty_price"]
    high = price_from_spec(spec, 0.12, SETTLEMENT)["dirty_price"]
    assert low > high


def test_zero_coupon_price_matches_discount_factor():
    spec = zero_bond(years=1)
    flows = calculate_cashflows(spec, SETTLEMENT)
    result = calculate_bond_price(flows, 0.10, SETTLEMENT, frequency=1)
    years = 365 / 365.0
    assert result["dirty_price"] == pytest.approx(100.0 / (1.10**years), rel=1e-9)


def test_price_returns_none_without_yield():
    spec = annual_bond()
    flows = calculate_cashflows(spec, SETTLEMENT)
    assert calculate_bond_price(flows, None, SETTLEMENT) is None


# -- current yield ---------------------------------------------------------

def test_current_yield():
    assert calculate_current_yield(80.0, 0.10, 100.0) == pytest.approx(0.125)


def test_current_yield_missing_input_is_none():
    assert calculate_current_yield(None, 0.10) is None
    assert calculate_current_yield(95.0, None) is None
    assert calculate_current_yield(0.0, 0.10) is None


# -- YTM -------------------------------------------------------------------

def test_ytm_round_trips_through_price():
    spec = annual_bond(years=7, coupon=0.12)
    flows = calculate_cashflows(spec, SETTLEMENT)
    target = 0.0935
    price = calculate_bond_price(flows, target, SETTLEMENT, frequency=1)["dirty_price"]
    solved = calculate_ytm(flows, price, SETTLEMENT, frequency=1)
    assert solved == pytest.approx(target, abs=1e-6)


def test_ytm_above_coupon_when_priced_below_par():
    spec = annual_bond(years=5, coupon=0.10)
    flows = calculate_cashflows(spec, SETTLEMENT)
    ytm = calculate_ytm(flows, 90.0, SETTLEMENT, frequency=1)
    assert ytm is not None and ytm > 0.10


def test_ytm_is_negative_when_the_price_exceeds_total_payments():
    spec = annual_bond(years=5, coupon=0.10)
    flows = calculate_cashflows(spec, SETTLEMENT)
    total_payments = sum(f.total_amount for f in flows)
    ytm = calculate_ytm(flows, total_payments * 1.5, SETTLEMENT, frequency=1)
    # Overpaying that much is a real, if unattractive, negative yield - not an
    # error, and certainly not a fabricated positive number.
    assert ytm is not None and ytm < 0


def test_ytm_none_for_an_unsolvable_price():
    spec = annual_bond()
    flows = calculate_cashflows(spec, SETTLEMENT)
    # Beyond the present value at the lowest yield the solver will consider.
    assert calculate_ytm(flows, 1e18, SETTLEMENT, frequency=1) is None
    assert calculate_ytm(flows, None, SETTLEMENT) is None
    assert calculate_ytm(flows, -5.0, SETTLEMENT) is None


# -- accrued interest ------------------------------------------------------

def test_accrued_interest_grows_within_the_period():
    spec = BondSpec(
        maturity_date=date(2030, 1, 1),
        coupon_rate=0.12,
        coupon_frequency=2,
        nominal=1000.0,
        issue_date=date(2024, 1, 1),
        day_count="ACT/365F",
    )
    start = calculate_accrued_interest(spec, date(2025, 1, 1))
    middle = calculate_accrued_interest(spec, date(2025, 3, 2))
    assert start == pytest.approx(0.0, abs=1e-9)
    # 60 days of a 12 % annual coupon on 1000.
    assert middle == pytest.approx(1000 * 0.12 * 60 / 365, rel=1e-9)


def test_zero_coupon_has_no_accrued_interest():
    assert calculate_accrued_interest(zero_bond(), SETTLEMENT) == 0.0


# -- duration and convexity -------------------------------------------------

def test_zero_coupon_macaulay_duration_equals_maturity():
    spec = zero_bond(years=3)
    flows = calculate_cashflows(spec, SETTLEMENT)
    duration = calculate_duration(flows, 0.10, SETTLEMENT, frequency=1)
    expected = (date(2028, 1, 1) - SETTLEMENT).days / 365.0
    assert duration == pytest.approx(expected, rel=1e-9)


def test_coupon_bond_duration_is_shorter_than_maturity():
    spec = annual_bond(years=10, coupon=0.12)
    flows = calculate_cashflows(spec, SETTLEMENT)
    duration = calculate_duration(flows, 0.12, SETTLEMENT, frequency=1)
    assert duration is not None and 0 < duration < 10


def test_modified_duration_is_macaulay_discounted():
    assert calculate_modified_duration(5.0, 0.10, 1) == pytest.approx(5.0 / 1.10)
    assert calculate_modified_duration(5.0, 0.10, 2) == pytest.approx(5.0 / 1.05)
    assert calculate_modified_duration(None, 0.10) is None
    assert calculate_modified_duration(5.0, None) is None


def test_zero_coupon_convexity_matches_closed_form():
    spec = zero_bond(years=2)
    flows = calculate_cashflows(spec, SETTLEMENT)
    ytm = 0.10
    convexity = calculate_convexity(flows, ytm, SETTLEMENT, frequency=1)
    t = (date(2027, 1, 1) - SETTLEMENT).days / 365.0
    expected = t * (t + 1.0) / (1 + ytm) ** 2
    assert convexity == pytest.approx(expected, rel=1e-9)


def test_convexity_is_positive_for_a_plain_bond():
    spec = annual_bond(years=8, coupon=0.11)
    flows = calculate_cashflows(spec, SETTLEMENT)
    assert calculate_convexity(flows, 0.11, SETTLEMENT, frequency=1) > 0


# -- spreads and pull to par -------------------------------------------------

def test_credit_spread():
    assert calculate_credit_spread(0.145, 0.121) == pytest.approx(0.024)
    assert calculate_credit_spread(None, 0.121) is None
    assert calculate_credit_spread(0.145, None) is None


def test_bid_ask_spread():
    result = calculate_bid_ask_spread(99.0, 101.0)
    assert result["absolute"] == pytest.approx(2.0)
    assert result["mid"] == pytest.approx(100.0)
    assert result["pct"] == pytest.approx(0.02)


def test_bid_ask_spread_rejects_crossed_and_missing_quotes():
    assert calculate_bid_ask_spread(101.0, 99.0) is None
    assert calculate_bid_ask_spread(None, 99.0) is None
    assert calculate_bid_ask_spread(99.0, None) is None


def test_pull_to_par():
    result = calculate_pull_to_par(90.0, 2.0, 100.0)
    assert result["total"] == pytest.approx(100 / 90 - 1)
    assert result["annualized"] == pytest.approx((100 / 90) ** 0.5 - 1)


def test_pull_to_par_is_negative_above_par():
    result = calculate_pull_to_par(105.0, 3.0, 100.0)
    assert result["annualized"] < 0


def test_pull_to_par_missing_inputs():
    assert calculate_pull_to_par(None, 2.0) is None
    assert calculate_pull_to_par(90.0, None) is None
    assert calculate_pull_to_par(90.0, 0.0) is None


class TestYieldToCallAndPut:
    """Early-redemption yields (§14). Absent a schedule, the answer is None."""

    SPEC = BondSpec(
        maturity_date=date(2030, 6, 18),
        coupon_rate=0.11,
        coupon_frequency=2,
        nominal=1000.0,
        next_coupon_date=date(2026, 12, 18),
        day_count="ACT/360",
    )
    SETTLEMENT = date(2026, 8, 13)

    def test_no_call_date_means_no_ytc_rather_than_ytm_in_disguise(self):
        assert calculate_ytc(self.SPEC, 950.0, self.SETTLEMENT, None) is None
        assert calculate_ytp(self.SPEC, 950.0, self.SETTLEMENT, None) is None

    def test_call_at_par_on_a_discount_bond_beats_yield_to_maturity(self):
        # Bought below par: getting par back sooner raises the return.
        price = 900.0
        ytm = calculate_ytm(
            calculate_cashflows(self.SPEC, self.SETTLEMENT),
            price,
            self.SETTLEMENT,
            frequency=2,
            day_count=self.SPEC.day_count,
        )
        ytc = calculate_ytc(self.SPEC, price, self.SETTLEMENT, date(2028, 6, 18))
        assert ytc is not None and ytm is not None
        assert ytc > ytm

    def test_call_premium_raises_the_yield_further(self):
        at_par = calculate_ytc(self.SPEC, 900.0, self.SETTLEMENT, date(2028, 6, 18))
        with_premium = calculate_ytc(
            self.SPEC, 900.0, self.SETTLEMENT, date(2028, 6, 18), 1050.0
        )
        assert with_premium > at_par

    def test_redemption_in_the_past_is_refused(self):
        assert calculate_ytc(self.SPEC, 950.0, self.SETTLEMENT, date(2020, 1, 1)) is None

    def test_put_and_call_agree_when_the_terms_are_identical(self):
        ytc = calculate_ytc(self.SPEC, 950.0, self.SETTLEMENT, date(2028, 6, 18))
        ytp = calculate_ytp(self.SPEC, 950.0, self.SETTLEMENT, date(2028, 6, 18))
        assert ytc == pytest.approx(ytp)
