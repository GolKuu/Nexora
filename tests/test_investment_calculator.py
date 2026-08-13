"""The investment calculator - the arithmetic a retail investor acts on.

The tests that matter most here are the ones guarding against the three
classic lies: counting returned principal as profit, pricing a purchase off
the last trade as though it were available, and subtracting inflation instead
of deflating by it.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.calculations.types import BondSpec
from app.services.investment_calculator import (
    Commission,
    InvestmentRequest,
    MarketSnapshot,
    assess_liquidity,
    calculate_investment,
    select_purchase_price,
)

SETTLEMENT = date(2026, 8, 13)


@pytest.fixture
def spec() -> BondSpec:
    """A plain 11 % semi-annual bond, par 1 000, maturing in ~4 years."""
    return BondSpec(
        maturity_date=date(2030, 6, 18),
        coupon_rate=0.11,
        coupon_frequency=2,
        nominal=1000.0,
        issue_date=date(2020, 6, 18),
        next_coupon_date=date(2026, 12, 18),
        coupon_type="fixed",
        day_count="ACT/360",
    )


@pytest.fixture
def market() -> MarketSnapshot:
    return MarketSnapshot(
        ask=88.5774,
        bid=83.4989,
        last=88.5775,
        turnover=900_000.0,
        number_of_trades=12,
        last_trade_date=date(2026, 8, 11),
        modified_duration=2.95,
        convexity=11.27,
        source="kase_public_api",
        data_mode="end_of_day",
    )


def run(spec, market, **kwargs):
    request = InvestmentRequest(settlement=SETTLEMENT, **kwargs)
    return calculate_investment(spec, market, request, identifier="TEST", currency="KZT")


class TestPriceSelection:
    def test_ask_is_preferred_and_needs_no_warning(self, market):
        price, basis, warning = select_purchase_price(market)
        assert (price, basis, warning) == (market.ask, "ask", None)

    def test_last_trade_is_used_but_flagged(self):
        price, basis, warning = select_purchase_price(MarketSnapshot(last=99.0))
        assert price == 99.0
        assert basis == "last"
        assert warning is not None and "не гарантированная цена" in warning

    def test_bid_only_is_flagged_more_strongly(self):
        _, basis, warning = select_purchase_price(MarketSnapshot(bid=90.0))
        assert basis == "bid"
        assert "не получится" in warning

    def test_no_price_at_all(self):
        assert select_purchase_price(MarketSnapshot()) == (None, None, None)

    def test_calculation_refuses_without_any_price(self, spec):
        result = run(spec, MarketSnapshot(), amount=1_000_000)
        assert result["quantity"] == 0
        assert result["price_basis"] is None
        assert "невозможно" in result["warnings"][0]


class TestQuantityAndCost:
    def test_whole_bonds_only_with_remainder_returned(self, spec, market):
        result = run(spec, market, amount=1_000_000)
        assert result["quantity"] == int(result["quantity"])
        assert result["total_purchase_cost"] <= 1_000_000
        assert result["cash_remaining"] == pytest.approx(
            1_000_000 - result["total_purchase_cost"], abs=0.01
        )
        assert result["cash_remaining"] >= 0

    def test_cost_decomposes_exactly(self, spec, market):
        result = run(
            spec, market, amount=5_000_000,
            commission=Commission("percent", 0.1),
        )
        assert result["total_purchase_cost"] == pytest.approx(
            result["principal_cost"]
            + result["accrued_interest_total"]
            + result["commission"],
            abs=0.02,
        )

    def test_percent_commission_is_charged_on_the_full_amount(self, spec, market):
        result = run(
            spec, market, amount=5_000_000,
            commission=Commission("percent", 0.1),
        )
        gross = result["principal_cost"] + result["accrued_interest_total"]
        assert result["commission"] == pytest.approx(gross * 0.001, abs=0.02)

    def test_fixed_commission_reduces_the_budget(self, spec, market):
        free = run(spec, market, amount=1_000_000)
        charged = run(
            spec, market, amount=1_000_000, commission=Commission("fixed", 50_000)
        )
        assert charged["quantity"] < free["quantity"]
        assert charged["commission"] == 50_000

    def test_lot_size_is_respected(self, spec, market):
        result = run(spec, market, amount=1_000_000, lot_size=10)
        assert result["quantity"] % 10 == 0

    def test_scales_to_a_very_large_order(self, spec, market):
        result = run(spec, market, amount=250_000_000)
        assert result["quantity"] > 200_000
        assert result["total_purchase_cost"] <= 250_000_000


class TestInsufficientFunds:
    def test_reports_the_minimum_instead_of_a_bare_zero(self, spec, market):
        result = run(spec, market, amount=100)
        assert result["quantity"] == 0
        assert result["minimum_required_amount"] > 100
        assert "Недостаточно средств" in result["warnings"][0]
        # The money was not silently consumed.
        assert result["cash_remaining"] == 100

    def test_minimum_covers_one_lot_including_commission(self, spec, market):
        result = run(
            spec, market, amount=10,
            lot_size=10, commission=Commission("percent", 0.1),
        )
        # 10 bonds at ~903 each plus commission.
        assert result["minimum_required_amount"] == pytest.approx(9038, rel=0.01)


class TestProfitVersusCashReceived:
    def test_returned_principal_is_not_profit(self, spec, market):
        result = run(spec, market, amount=5_000_000)
        assert result["principal_repayment"] > 0
        # Profit is strictly what came back minus what was paid.
        assert result["total_profit"] == pytest.approx(
            result["total_cash_received"] - result["total_purchase_cost"], abs=0.02
        )
        # And it is far smaller than the gross receipts.
        assert result["total_profit"] < result["total_cash_received"]

    def test_cash_received_is_coupons_plus_principal(self, spec, market):
        result = run(spec, market, amount=5_000_000)
        assert result["total_cash_received"] == pytest.approx(
            result["coupon_income"] + result["principal_repayment"], abs=0.02
        )

    def test_price_return_at_maturity_is_the_pull_to_par(self, spec, market):
        result = run(spec, market, amount=5_000_000)
        assert result["estimated_price_return"] == pytest.approx(
            result["principal_repayment"] - result["principal_cost"], abs=0.02
        )

    def test_cashflow_schedule_is_populated_and_typed(self, spec, market):
        result = run(spec, market, amount=5_000_000)
        flows = result["cashflows"]
        assert flows
        assert all(f["type"] in {"coupon", "principal", "coupon_and_principal"} for f in flows)
        assert flows[-1]["type"] == "coupon_and_principal"
        assert flows[-1]["principal_amount"] > 0


class TestInflation:
    def test_real_return_uses_compounding_not_subtraction(self, spec, market):
        result = run(
            spec, market, amount=5_000_000,
            inflation_enabled=True, inflation_rate=0.102,
        )
        total = result["total_return_percent"] / 100
        years = result["holding_period_years"]
        expected = (1 + total) / (1.102**years) - 1
        assert result["real_return_percent"] == pytest.approx(expected * 100, abs=0.01)
        # The naive answer would be materially different; make sure we avoided it.
        naive = total - 0.102
        assert result["real_return_percent"] / 100 != pytest.approx(naive, abs=1e-6)

    def test_real_is_below_nominal_when_inflation_is_positive(self, spec, market):
        result = run(
            spec, market, amount=5_000_000,
            inflation_enabled=True, inflation_rate=0.102,
        )
        assert result["real_return_percent"] < result["total_return_percent"]
        assert result["real_profit"] < result["total_profit"]

    def test_disabling_inflation_nulls_real_but_not_nominal(self, spec, market):
        on = run(spec, market, amount=5_000_000, inflation_enabled=True, inflation_rate=0.102)
        off = run(spec, market, amount=5_000_000, inflation_enabled=False)
        assert off["real_return_percent"] is None
        assert off["real_profit"] is None
        assert off["inflation_rate_percent"] is None
        # Nominal figures are untouched by the switch.
        assert off["total_return_percent"] == pytest.approx(on["total_return_percent"])
        assert off["quantity"] == on["quantity"]

    def test_missing_inflation_reading_is_not_treated_as_zero(self, spec, market):
        result = run(spec, market, amount=5_000_000, inflation_enabled=True, inflation_rate=None)
        assert result["real_return_percent"] is None


class TestLiquidity:
    def test_order_far_above_turnover_warns(self, market):
        warning = assess_liquidity(market, 500_000_000, SETTLEMENT)
        assert warning is not None and "оборота" in warning

    def test_normal_order_in_a_liquid_bond_is_silent(self, market):
        assert assess_liquidity(market, 100_000, SETTLEMENT) is None

    def test_stale_bond_warns(self):
        stale = MarketSnapshot(
            last=100.0, turnover=10_000_000.0,
            number_of_trades=5, last_trade_date=date(2026, 1, 1),
        )
        assert "не было сделок" in assess_liquidity(stale, 1_000, SETTLEMENT)

    def test_large_order_surfaces_on_the_result(self, spec, market):
        result = run(spec, market, amount=250_000_000)
        assert result["liquidity_warning"] is not None


class TestEarlyExit:
    def test_selling_early_produces_a_sale_flow_and_shorter_horizon(self, spec, market):
        held = run(spec, market, amount=5_000_000)
        sold = run(
            spec, market, amount=5_000_000,
            exit_mode="date", exit_date=date(2028, 8, 13),
        )
        assert sold["holding_period_years"] < held["holding_period_years"]
        assert sold["cashflows"][-1]["type"] == "sale"
        assert any("оценка по сценарию" in w for w in sold["warnings"])

    def test_exit_after_maturity_is_clamped(self, spec, market):
        result = run(
            spec, market, amount=1_000_000,
            exit_mode="date", exit_date=date(2040, 1, 1),
        )
        assert result["exit_date"] == spec.maturity_date.isoformat()

    def test_bad_scenario_is_not_better_than_good(self, spec, market):
        bad = run(spec, market, amount=5_000_000, exit_mode="date",
                  exit_date=date(2028, 8, 13), scenario="bad")
        good = run(spec, market, amount=5_000_000, exit_mode="date",
                   exit_date=date(2028, 8, 13), scenario="good")
        assert bad["total_return_percent"] <= good["total_return_percent"]


class TestResponseContract:
    def test_every_documented_field_is_present(self, spec, market):
        result = run(spec, market, amount=5_000_000)
        for field in (
            "bond_identifier", "input_amount", "quantity",
            "unit_clean_price", "unit_dirty_price", "accrued_interest_per_bond",
            "principal_cost", "accrued_interest_total", "commission",
            "total_purchase_cost", "cash_remaining", "minimum_required_amount",
            "coupon_income", "principal_repayment", "estimated_price_return",
            "total_profit", "total_cash_received",
            "total_return_percent", "annualized_return_percent",
            "real_profit", "real_return_percent",
            "cashflows", "liquidity_warning", "warnings",
            "data_timestamp", "source", "price_basis",
        ):
            assert field in result, field

    def test_warnings_always_state_the_unmodelled_costs(self, spec, market):
        result = run(spec, market, amount=5_000_000)
        text = " ".join(result["warnings"])
        assert "Налоги" in text
        assert "реинвестируются" in text
