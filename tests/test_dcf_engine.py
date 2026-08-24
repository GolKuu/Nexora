from __future__ import annotations

import pytest

from app.dcf.engine import (DCFValidationError, DCFValuationEngine,
    ScenarioAssumptions, ValuationInput, calculate_wacc)


def assumptions(**changes) -> ScenarioAssumptions:
    values = dict(revenue_growth=(0.05,)*5, ebit_margin=0.15, tax_rate=0.20,
        da_pct_sales=0.03, capex_pct_sales=0.04, nwc_pct_sales=0.10,
        wacc=0.12, terminal_growth=0.04)
    values.update(changes)
    return ScenarioAssumptions(**values)


def test_fcff_discount_terminal_and_equity_are_known() -> None:
    result = DCFValuationEngine().calculate(
        ValuationInput(revenue=1_000, net_debt=100, shares_outstanding=10),
        assumptions(),
    )
    first = result["forecast"][0]
    assert first["revenue"] == pytest.approx(1_050)
    assert first["nopat"] == pytest.approx(126)
    assert first["fcff"] == pytest.approx(110.5)
    assert first["pv_fcff"] == pytest.approx(98.660714)
    assert result["enterprise_value"] == pytest.approx(1426.14532, rel=1e-5)
    assert result["equity_value"] == pytest.approx(1326.14532, rel=1e-5)
    assert result["fair_value_per_share"] == pytest.approx(132.614532, rel=1e-5)


def test_wacc_formula_is_deterministic() -> None:
    assert calculate_wacc(.05, .06, 1.2, .08, .20, 800, 200) == pytest.approx(.1104)


def test_wacc_must_exceed_terminal_growth() -> None:
    with pytest.raises(DCFValidationError, match="WACC"):
        DCFValuationEngine().calculate(ValuationInput(1000, 0, 10), assumptions(wacc=.04, terminal_growth=.04))


def test_invalid_share_count_and_extreme_growth_are_rejected() -> None:
    with pytest.raises(DCFValidationError, match="Shares"):
        DCFValuationEngine().calculate(ValuationInput(1000, 0, 0), assumptions())
    with pytest.raises(DCFValidationError, match="growth"):
        DCFValuationEngine().calculate(ValuationInput(1000, 0, 10), assumptions(revenue_growth=(1.0,)*5))


def test_scenarios_recalculate_and_validate_order() -> None:
    inputs = ValuationInput(1000, 100, 10)
    scenarios = {
        "bear": assumptions(revenue_growth=(.01,)*5, ebit_margin=.12, wacc=.15, terminal_growth=.02),
        "base": assumptions(),
        "bull": assumptions(revenue_growth=(.08,)*5, ebit_margin=.18, wacc=.10, terminal_growth=.05),
    }
    result = DCFValuationEngine().calculate_scenarios(inputs, scenarios)
    assert result["bear"]["fair_value_per_share"] < result["base"]["fair_value_per_share"] < result["bull"]["fair_value_per_share"]
    with pytest.raises(DCFValidationError, match="ordering"):
        DCFValuationEngine().calculate_scenarios(inputs, {"bear": scenarios["bull"], "base": scenarios["base"], "bull": scenarios["bear"]})


def test_same_snapshot_produces_bitwise_same_numeric_result() -> None:
    engine = DCFValuationEngine(); inputs = ValuationInput(1000, 100, 10); config = assumptions()
    assert engine.calculate(inputs, config) == engine.calculate(inputs, config)
