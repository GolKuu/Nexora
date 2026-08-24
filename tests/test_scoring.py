from __future__ import annotations

import pytest

from app.scoring.context import ScoringContext
from app.scoring.engine import ScoringEngine
from app.scoring.explain import explain_score
from app.scoring.normalizers import banded, grade_to_score, linear, rating_to_grade
from app.scoring.weights import get_weights


def strong_corporate() -> ScoringContext:
    return ScoringContext(
        bond_id=1,
        ticker="TEST",
        bond_type="corporate",
        coupon_rate=0.14,
        coupon_type="fixed",
        coupon_frequency=2,
        outstanding_amount=3e10,
        years_to_maturity=4.0,
        clean_price=98.0,
        ytm=0.145,
        bid=97.8,
        ask=98.2,
        bid_ask_spread_pct=0.004,
        avg_daily_turnover_30d=8e7,
        trading_days_30d=18,
        price_volatility_90d=0.012,
        modified_duration=3.2,
        convexity=14.0,
        credit_spread=0.02,
        real_ytm=0.035,
        inflation_rate=0.105,
        quote_age_hours=3.0,
        rating_grade=9,
        net_debt_to_ebitda=1.4,
        interest_coverage=7.5,
        current_ratio=1.8,
        quick_ratio=1.1,
        operating_cash_flow=1.5e11,
        free_cash_flow=6e10,
        roa=0.06,
        roe=0.14,
        ebitda_margin=0.26,
        debt_to_equity=0.6,
        revenue_growth=0.12,
        profit_growth=0.18,
        peer_count=6,
        peer_median_ytm=0.13,
        peer_median_spread=0.012,
        data_mode="delayed",
    )


def weak_corporate() -> ScoringContext:
    ctx = strong_corporate()
    ctx.rating_grade = 16
    ctx.net_debt_to_ebitda = 6.2
    ctx.interest_coverage = 1.1
    ctx.current_ratio = 0.7
    ctx.quick_ratio = 0.3
    ctx.free_cash_flow = -4e9
    ctx.roa = -0.01
    ctx.roe = -0.05
    ctx.ebitda_margin = 0.04
    ctx.debt_to_equity = 3.4
    ctx.revenue_growth = -0.15
    ctx.profit_growth = -0.30
    ctx.real_ytm = -0.02
    ctx.bid_ask_spread_pct = 0.045
    ctx.avg_daily_turnover_30d = 2e5
    ctx.trading_days_30d = 1
    return ctx


def bank_context() -> ScoringContext:
    ctx = strong_corporate()
    ctx.bond_type = "bank"
    ctx.is_financial_institution = True
    ctx.issuer_sector = "bank"
    ctx.capital_adequacy_ratio = 0.17
    ctx.tier1_ratio = 0.145
    ctx.npl_ratio = 0.037
    ctx.provision_coverage = 1.25
    ctx.loan_to_deposit = 0.80
    ctx.liquid_assets_ratio = 0.22
    ctx.net_interest_margin = 0.05
    ctx.cost_to_income = 0.38
    ctx.equity_to_assets = 0.11
    return ctx


# -- normalizers ------------------------------------------------------------

def test_linear_maps_between_worst_and_best():
    assert linear(0.0, 0.0, 10.0) == 0.0
    assert linear(10.0, 0.0, 10.0) == 100.0
    assert linear(5.0, 0.0, 10.0) == pytest.approx(50.0)
    # Reversed direction: lower is better.
    assert linear(0.0, 10.0, 0.0) == 100.0
    assert linear(None, 0.0, 10.0) is None


def test_linear_clamps_outside_the_range():
    assert linear(20.0, 0.0, 10.0) == 100.0
    assert linear(-5.0, 0.0, 10.0) == 0.0


def test_banded_interpolates_and_clamps():
    bands = [(0.0, 100.0), (2.0, 50.0), (4.0, 0.0)]
    assert banded(0.0, bands) == 100.0
    assert banded(1.0, bands) == pytest.approx(75.0)
    assert banded(3.0, bands) == pytest.approx(25.0)
    assert banded(99.0, bands) == 0.0
    assert banded(-1.0, bands) == 100.0
    assert banded(None, bands) is None


def test_rating_ladder():
    assert rating_to_grade("AAA") == 1
    assert rating_to_grade("bbb-") == 10
    assert rating_to_grade("не рейтинг") is None
    assert grade_to_score(1) == 100.0
    assert grade_to_score(21) == 0.0
    assert grade_to_score(None) is None


# -- engine -----------------------------------------------------------------

@pytest.mark.parametrize("ctx", [strong_corporate(), weak_corporate(), bank_context()])
def test_all_scores_stay_within_bounds(ctx):
    results = ScoringEngine().compute_all(ctx)
    for kind, result in results.items():
        if result.value is not None:
            assert 0.0 <= result.value <= 100.0, f"{kind} out of range: {result.value}"
        if result.confidence is not None:
            assert 0.0 <= result.confidence <= 1.0


def test_every_declared_score_kind_is_produced():
    results = ScoringEngine().compute_all(strong_corporate())
    for kind in (
        "investment", "credit", "liquidity", "growth", "income", "real_return",
        "risk_reward", "stability", "exit", "relative_value", "data_quality",
        "analysis_confidence", "hold", "trade",
    ):
        assert kind in results


def test_a_strong_issuer_outscores_a_weak_one():
    strong = ScoringEngine().compute_all(strong_corporate())
    weak = ScoringEngine().compute_all(weak_corporate())
    assert strong["credit"].value > weak["credit"].value
    assert strong["investment"].value > weak["investment"].value
    assert strong["liquidity"].value > weak["liquidity"].value


def test_bank_uses_the_bank_credit_model():
    ctx = bank_context()
    result = ScoringEngine().credit(ctx, __import__("datetime").datetime.now())
    assert result.inputs["credit_model"] == "bank"
    codes = {c.code for c in result.components}
    assert "capital_adequacy" in codes
    # Debt/EBITDA must not appear anywhere in a bank's credit score.
    assert "leverage" not in codes


def test_corporate_uses_the_corporate_credit_model():
    result = ScoringEngine().credit(
        strong_corporate(), __import__("datetime").datetime.now()
    )
    assert result.inputs["credit_model"] == "corporate"
    codes = {c.code for c in result.components}
    assert "leverage" in codes
    assert "capital_adequacy" not in codes


def test_subordinated_issue_scores_lower_than_senior():
    senior = strong_corporate()
    subordinated = strong_corporate()
    subordinated.subordinated = True
    now = __import__("datetime").datetime.now()
    engine = ScoringEngine()
    assert engine.credit(subordinated, now).value < engine.credit(senior, now).value


def test_empty_context_produces_no_fabricated_scores():
    results = ScoringEngine().compute_all(ScoringContext())
    assert results["credit"].value is None
    assert results["real_return"].value is None
    # An unscoreable bond must not come out as zero, which reads as "terrible".
    assert results["investment"].value is None or results["investment"].value > 0


@pytest.mark.parametrize("missing", ["ytm", "years_to_maturity"])
def test_missing_critical_market_basis_cannot_produce_investment_score(missing):
    ctx = strong_corporate()
    setattr(ctx, missing, None)
    results = ScoringEngine().compute_all(ctx)
    assert results["investment"].value is None
    assert results["investment"].confidence == 0
    assert results["hold"].value is None
    assert results["data_quality"].value <= 40


def test_missing_data_lowers_confidence():
    full = ScoringEngine().compute_all(strong_corporate())
    sparse_ctx = strong_corporate()
    sparse_ctx.net_debt_to_ebitda = None
    sparse_ctx.interest_coverage = None
    sparse_ctx.roa = sparse_ctx.roe = sparse_ctx.ebitda_margin = None
    sparse = ScoringEngine().compute_all(sparse_ctx)
    assert sparse["credit"].confidence < full["credit"].confidence


def test_mock_data_halves_the_data_quality_score():
    real = strong_corporate()
    mock = strong_corporate()
    mock.data_mode = "mock"
    engine = ScoringEngine()
    now = __import__("datetime").datetime.now()
    assert engine.data_quality(mock, now).value < engine.data_quality(real, now).value
    assert "Демонстрационные" in engine.data_quality(mock, now).notes


def test_risk_profile_reweights_the_investment_score_only():
    conservative = get_weights("conservative")
    aggressive = get_weights("aggressive")
    assert conservative.investment["credit_quality"] > aggressive.investment["credit_quality"]
    assert aggressive.investment["growth"] > conservative.investment["growth"]
    # Sub-model weights stay objective across profiles.
    assert conservative.credit_corporate == aggressive.credit_corporate


def test_investment_weights_sum_to_one():
    for profile in ("conservative", "balanced", "aggressive"):
        assert sum(get_weights(profile).investment.values()) == pytest.approx(1.0)


# -- explanation ------------------------------------------------------------

def test_explanation_lists_strengths_weaknesses_and_gaps():
    results = ScoringEngine().compute_all(weak_corporate())
    payload = explain_score(results["investment"])
    assert payload["value"] == results["investment"].value
    assert payload["verdict"]
    assert isinstance(payload["weaknesses"], list)
    assert isinstance(payload["missing_data"], list)
    assert len(payload["components"]) == len(results["investment"].components)


def test_explanation_handles_a_missing_score():
    results = ScoringEngine().compute_all(ScoringContext())
    payload = explain_score(results["real_return"])
    assert payload["value"] is None
    assert payload["verdict"] == "нет данных"
