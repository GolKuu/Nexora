from __future__ import annotations

import pytest

from app.calculations.portfolio import (
    PositionInput,
    calculate_portfolio_duration,
    calculate_portfolio_ytm,
    calculate_weighted_score,
)
from app.calculations.returns import (
    calculate_annualized_return,
    calculate_real_return,
    calculate_real_total_return,
    calculate_total_return,
)
from app.calculations.scenarios import calculate_scenario_price


def test_total_return():
    assert calculate_total_return(1000.0, 1250.0) == pytest.approx(0.25)
    assert calculate_total_return(0.0, 100.0) is None
    assert calculate_total_return(None, 100.0) is None


def test_annualized_return_is_geometric():
    assert calculate_annualized_return(0.21, 2.0) == pytest.approx(0.10, abs=1e-9)
    assert calculate_annualized_return(1.0, 0.0) is None


def test_real_return_uses_the_fisher_equation():
    real = calculate_real_return(0.15, 0.10)
    assert real == pytest.approx(1.15 / 1.10 - 1)
    # The naive subtraction overstates the result; we must not match it.
    assert real < 0.05


def test_real_return_is_negative_below_inflation():
    assert calculate_real_return(0.08, 0.12) < 0


def test_real_return_missing_inputs():
    assert calculate_real_return(None, 0.10) is None
    assert calculate_real_return(0.10, None) is None


def test_real_total_return_compounds_inflation_over_the_period():
    total = 0.60          # +60 % over 4 years, nominal
    inflation = 0.09
    years = 4.0
    expected = 1.60 / (1.09**4) - 1
    assert calculate_real_total_return(total, inflation, years) == pytest.approx(expected)


def test_real_total_return_matches_real_return_over_one_year():
    single = calculate_real_return(0.14, 0.10)
    multi = calculate_real_total_return(0.14, 0.10, 1.0)
    assert single == pytest.approx(multi)


# -- portfolio ---------------------------------------------------------------

def test_portfolio_ytm_is_value_weighted():
    positions = [
        PositionInput(market_value=300.0, ytm=0.10),
        PositionInput(market_value=100.0, ytm=0.20),
    ]
    assert calculate_portfolio_ytm(positions) == pytest.approx(0.125)


def test_portfolio_ignores_positions_without_data():
    positions = [
        PositionInput(market_value=100.0, ytm=0.10),
        PositionInput(market_value=100.0, ytm=None),
    ]
    # The unknown position must not be treated as a zero yield.
    assert calculate_portfolio_ytm(positions) == pytest.approx(0.10)
    assert calculate_portfolio_ytm([PositionInput(market_value=None, ytm=None)]) is None


def test_portfolio_duration():
    positions = [
        PositionInput(market_value=100.0, modified_duration=2.0),
        PositionInput(market_value=300.0, modified_duration=6.0),
    ]
    assert calculate_portfolio_duration(positions) == pytest.approx(5.0)


# -- weighted score ----------------------------------------------------------

def test_weighted_score_renormalises_over_available_components():
    result = calculate_weighted_score([("a", 80.0, 0.5), ("b", None, 0.5)])
    assert result["value"] == pytest.approx(80.0)
    assert result["coverage"] == pytest.approx(0.5)


def test_weighted_score_stays_within_bounds():
    result = calculate_weighted_score([("a", 100.0, 0.5), ("b", 100.0, 0.5)])
    assert result["value"] == 100.0
    result = calculate_weighted_score([("a", 0.0, 1.0)])
    assert result["value"] == 0.0


def test_weighted_score_none_when_nothing_available():
    assert calculate_weighted_score([("a", None, 1.0)]) is None
    assert calculate_weighted_score([]) is None


# -- scenarios ---------------------------------------------------------------

def test_scenario_price_falls_when_yields_rise():
    price = calculate_scenario_price(100.0, 5.0, 30.0, 0.01)
    assert price == pytest.approx(100.0 * (1 - 5.0 * 0.01 + 0.5 * 30.0 * 0.0001))
    assert price < 100.0


def test_scenario_price_rises_when_yields_fall():
    assert calculate_scenario_price(100.0, 5.0, 30.0, -0.01) > 100.0


def test_scenario_price_needs_duration():
    assert calculate_scenario_price(100.0, None, 30.0, 0.01) is None
