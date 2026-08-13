from __future__ import annotations

from datetime import date

import pytest

from app.calculations.cashflows import (
    calculate_cashflows,
    next_coupon_date,
    previous_coupon_date,
)
from app.calculations.daycount import add_months, year_fraction
from app.calculations.types import BondSpec, CouponPeriod


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


class TestPublishedSchedule:
    """KASE publishes the real coupon schedule; it outranks any projection."""

    MATURITY = date(2030, 6, 18)

    def _spec(self, **overrides):
        base = dict(
            maturity_date=self.MATURITY,
            coupon_rate=0.11,
            coupon_frequency=2,
            nominal=1000.0,
            issue_date=date(2020, 6, 18),
            day_count="ACT/360",
        )
        base.update(overrides)
        return BondSpec(**base)

    def _schedule(self, dates, rates=None):
        rates = rates or [0.11] * len(dates)
        return tuple(
            CouponPeriod(payment_date=d, rate=r) for d, r in zip(dates, rates)
        )

    def test_published_dates_are_used_verbatim(self):
        # Deliberately irregular: a rolled projection would not produce these.
        published = [date(2026, 12, 20), date(2027, 6, 22), self.MATURITY]
        spec = self._spec(schedule=self._schedule(published))
        flows = calculate_cashflows(spec, date(2026, 8, 13))
        assert [f.payment_date for f in flows] == published

    def test_published_flows_are_not_marked_estimated(self):
        spec = self._spec(
            coupon_type="floating",
            schedule=self._schedule([date(2026, 12, 18), self.MATURITY]),
        )
        flows = calculate_cashflows(spec, date(2026, 8, 13))
        # A floating bond with published fixings is a fact, not a projection.
        assert all(not f.is_estimated for f in flows)

    def test_unfixed_future_period_stays_estimated(self):
        spec = self._spec(
            schedule=(
                CouponPeriod(payment_date=date(2026, 12, 18), rate=0.11),
                CouponPeriod(payment_date=self.MATURITY, rate=None),
            )
        )
        flows = calculate_cashflows(spec, date(2026, 8, 13))
        assert flows[0].is_estimated is False
        assert flows[-1].is_estimated is True

    def test_per_period_rates_change_the_coupon(self):
        spec = self._spec(
            schedule=self._schedule(
                [date(2026, 12, 18), date(2027, 6, 18), self.MATURITY],
                rates=[0.11, 0.20, 0.05],
            )
        )
        flows = calculate_cashflows(spec, date(2026, 8, 13))
        # 1000 nominal, semi-annual: rate/2 per payment.
        assert flows[0].coupon_amount == pytest.approx(55.0)
        assert flows[1].coupon_amount == pytest.approx(100.0)
        assert flows[2].coupon_amount == pytest.approx(25.0)

    def test_principal_is_attached_to_the_final_payment(self):
        spec = self._spec(schedule=self._schedule([date(2026, 12, 18), self.MATURITY]))
        flows = calculate_cashflows(spec, date(2026, 8, 13))
        assert flows[0].principal_amount == 0.0
        assert flows[-1].principal_amount == pytest.approx(1000.0)

    def test_past_periods_are_excluded(self):
        spec = self._spec(
            schedule=self._schedule(
                [date(2021, 6, 18), date(2026, 12, 18), self.MATURITY]
            )
        )
        flows = calculate_cashflows(spec, date(2026, 8, 13))
        assert all(f.payment_date > date(2026, 8, 13) for f in flows)


class TestFrequencyFromSchedule:
    """An unknown frequency used to be read as "zero-coupon" - a real bug."""

    def _spec(self, dates, frequency=None):
        return BondSpec(
            maturity_date=dates[-1],
            coupon_rate=0.20,
            coupon_frequency=frequency,
            nominal=1000.0,
            day_count="ACT/360",
            schedule=tuple(CouponPeriod(payment_date=d, rate=0.20) for d in dates),
        )

    def test_semiannual_spacing_is_recovered(self):
        spec = self._spec(
            [date(2026, 11, 21), date(2027, 5, 21), date(2027, 11, 21)]
        )
        assert spec.effective_frequency == 2

    def test_annual_spacing_is_recovered(self):
        spec = self._spec(
            [date(2026, 9, 10), date(2027, 9, 10), date(2028, 9, 10)]
        )
        assert spec.effective_frequency == 1

    def test_quarterly_and_monthly_spacing(self):
        quarterly = self._spec(
            [date(2026, 4, 30), date(2026, 7, 30), date(2026, 10, 30)]
        )
        monthly = self._spec(
            [date(2026, 4, 30), date(2026, 5, 30), date(2026, 6, 30)]
        )
        assert quarterly.effective_frequency == 4
        assert monthly.effective_frequency == 12

    def test_coupon_bond_with_unknown_frequency_is_not_zero_coupon(self):
        # The regression this guards: accrued interest reported as 0 on a
        # 20 % bond because the frequency field happened to be null.
        spec = self._spec([date(2026, 11, 21), date(2027, 5, 21)])
        assert spec.coupon_frequency is None
        assert spec.is_zero_coupon is False

    def test_a_genuine_zero_coupon_is_still_zero(self):
        spec = BondSpec(
            maturity_date=date(2027, 1, 1),
            coupon_rate=None,
            coupon_frequency=None,
            nominal=1000.0,
        )
        assert spec.is_zero_coupon is True

    def test_explicit_frequency_is_not_overridden(self):
        spec = self._spec([date(2026, 4, 30), date(2026, 7, 30)], frequency=4)
        assert spec.effective_frequency == 4
