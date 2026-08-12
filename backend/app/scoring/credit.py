"""Credit quality models.

Two distinct models, because a bank's balance sheet is not a smaller version of
an industrial company's. Applying Debt/EBITDA to a bank produces a number that
looks precise and means nothing, so the bank branch never touches it.
"""

from __future__ import annotations

from app.scoring.context import ScoringContext
from app.scoring.normalizers import average, banded, grade_to_score
from app.scoring.results import ComponentResult
from app.scoring.weights import COMPONENT_LABELS


def _c(code: str, value: float | None, weight: float, raw: float | None, unit: str | None,
       explanation: str | None = None) -> ComponentResult:
    return ComponentResult(
        code=code,
        label=COMPONENT_LABELS.get(code, code),
        value=value,
        weight=weight,
        raw_value=raw,
        raw_unit=unit,
        explanation=explanation,
    )


# --------------------------------------------------------------------------
# corporate
# --------------------------------------------------------------------------

def corporate_components(
    ctx: ScoringContext, weights: dict[str, float]
) -> list[ComponentResult]:
    leverage_raw = ctx.net_debt_to_ebitda if ctx.net_debt_to_ebitda is not None else ctx.debt_to_ebitda
    leverage = banded(
        leverage_raw,
        [(0.0, 100.0), (1.0, 92.0), (2.0, 80.0), (3.0, 62.0), (4.0, 42.0), (5.0, 25.0), (7.0, 5.0)],
    )
    coverage = banded(
        ctx.interest_coverage,
        [(0.0, 0.0), (1.0, 25.0), (2.0, 50.0), (3.0, 68.0), (5.0, 85.0), (10.0, 100.0)],
    )
    liquidity_ratios = average(
        [
            banded(ctx.current_ratio, [(0.5, 5.0), (1.0, 40.0), (1.5, 70.0), (2.0, 90.0), (3.0, 100.0)]),
            banded(ctx.quick_ratio, [(0.3, 5.0), (0.7, 40.0), (1.0, 72.0), (1.5, 92.0), (2.0, 100.0)]),
        ]
    )
    cash_generation = average(
        [
            None if ctx.operating_cash_flow is None else (85.0 if ctx.operating_cash_flow > 0 else 15.0),
            None if ctx.free_cash_flow is None else (85.0 if ctx.free_cash_flow > 0 else 25.0),
        ]
    )
    profitability = average(
        [
            banded(ctx.roa, [(-0.05, 0.0), (0.0, 35.0), (0.03, 60.0), (0.07, 82.0), (0.15, 100.0)]),
            banded(ctx.roe, [(-0.10, 0.0), (0.0, 35.0), (0.08, 62.0), (0.15, 85.0), (0.30, 100.0)]),
            banded(ctx.ebitda_margin, [(0.0, 10.0), (0.08, 40.0), (0.15, 65.0), (0.25, 85.0), (0.40, 100.0)]),
        ]
    )
    capital_structure = banded(
        ctx.debt_to_equity,
        [(0.0, 100.0), (0.5, 88.0), (1.0, 72.0), (2.0, 48.0), (3.0, 28.0), (5.0, 5.0)],
    )
    dynamics = average(
        [
            banded(ctx.revenue_growth, [(-0.20, 5.0), (-0.05, 35.0), (0.0, 55.0), (0.10, 78.0), (0.25, 100.0)]),
            banded(ctx.profit_growth, [(-0.30, 5.0), (-0.05, 40.0), (0.0, 55.0), (0.15, 80.0), (0.40, 100.0)]),
        ]
    )

    return [
        _c("leverage", leverage, weights["leverage"], leverage_raw, "x"),
        _c("interest_coverage", coverage, weights["interest_coverage"], ctx.interest_coverage, "x"),
        _c("liquidity_ratios", liquidity_ratios, weights["liquidity_ratios"], ctx.current_ratio, "x"),
        _c("cash_generation", cash_generation, weights["cash_generation"], ctx.free_cash_flow, ctx.currency),
        _c("profitability", profitability, weights["profitability"], ctx.roe, "%"),
        _c("capital_structure", capital_structure, weights["capital_structure"], ctx.debt_to_equity, "x"),
        _c("dynamics", dynamics, weights["dynamics"], ctx.revenue_growth, "%"),
    ]


# --------------------------------------------------------------------------
# banks and other financial institutions
# --------------------------------------------------------------------------

def bank_components(
    ctx: ScoringContext, weights: dict[str, float]
) -> list[ComponentResult]:
    capital = average(
        [
            banded(ctx.capital_adequacy_ratio, [(0.08, 0.0), (0.10, 35.0), (0.12, 60.0), (0.16, 85.0), (0.22, 100.0)]),
            banded(ctx.tier1_ratio, [(0.06, 0.0), (0.08, 35.0), (0.11, 65.0), (0.15, 88.0), (0.20, 100.0)]),
        ]
    )
    asset_quality = average(
        [
            banded(ctx.npl_ratio, [(0.0, 100.0), (0.02, 88.0), (0.05, 65.0), (0.08, 42.0), (0.15, 12.0), (0.25, 0.0)]),
            banded(ctx.provision_coverage, [(0.3, 10.0), (0.6, 45.0), (0.9, 75.0), (1.2, 95.0), (1.5, 100.0)]),
        ]
    )
    liquidity = average(
        [
            banded(ctx.liquid_assets_ratio, [(0.05, 0.0), (0.12, 40.0), (0.20, 70.0), (0.30, 92.0), (0.40, 100.0)]),
            # Both extremes are a warning sign: too high means the book is not lending.
            banded(ctx.loan_to_deposit, [(0.4, 55.0), (0.7, 90.0), (0.95, 75.0), (1.2, 40.0), (1.6, 10.0)]),
        ]
    )
    profitability = average(
        [
            banded(ctx.roa, [(-0.02, 0.0), (0.0, 35.0), (0.01, 60.0), (0.02, 82.0), (0.04, 100.0)]),
            banded(ctx.roe, [(-0.10, 0.0), (0.0, 35.0), (0.08, 62.0), (0.15, 85.0), (0.25, 100.0)]),
            banded(ctx.net_interest_margin, [(0.01, 10.0), (0.03, 50.0), (0.05, 78.0), (0.08, 100.0)]),
        ]
    )
    efficiency = banded(
        ctx.cost_to_income,
        [(0.25, 100.0), (0.40, 85.0), (0.55, 62.0), (0.70, 38.0), (0.90, 8.0)],
    )
    funding = average(
        [
            banded(ctx.equity_to_assets, [(0.03, 0.0), (0.06, 35.0), (0.10, 70.0), (0.15, 92.0), (0.20, 100.0)]),
            banded(ctx.loan_to_deposit, [(0.4, 70.0), (0.8, 90.0), (1.0, 70.0), (1.3, 35.0), (1.8, 5.0)]),
        ]
    )

    return [
        _c("capital_adequacy", capital, weights["capital_adequacy"], ctx.capital_adequacy_ratio, "%"),
        _c("asset_quality", asset_quality, weights["asset_quality"], ctx.npl_ratio, "%"),
        _c("liquidity", liquidity, weights["liquidity"], ctx.liquid_assets_ratio, "%"),
        _c("profitability", profitability, weights["profitability"], ctx.roe, "%"),
        _c("efficiency", efficiency, weights["efficiency"], ctx.cost_to_income, "%"),
        _c("funding_structure", funding, weights["funding_structure"], ctx.equity_to_assets, "%"),
    ]


def rating_component(ctx: ScoringContext, weight: float = 0.0) -> ComponentResult:
    return ComponentResult(
        code="agency_rating",
        label="Кредитный рейтинг",
        value=grade_to_score(ctx.rating_grade),
        weight=weight,
        raw_value=None if ctx.rating_grade is None else float(ctx.rating_grade),
        raw_unit="grade",
        explanation=None if not ctx.rating_agency else f"Рейтинг {ctx.rating_agency}",
    )


def structural_adjustment(ctx: ScoringContext) -> tuple[float, list[str]]:
    """Multiplier applied to the credit score for structural features.

    Seniority and state ownership genuinely change recovery expectations, so
    they adjust the score rather than being buried inside a ratio.
    """
    factor = 1.0
    notes: list[str] = []
    if ctx.subordinated:
        factor *= 0.90
        notes.append("Субординированный выпуск: в случае проблем выплаты идут в последнюю очередь.")
    if ctx.secured:
        factor *= 1.04
        notes.append("Выпуск обеспечен залогом.")
    if ctx.is_state_owned or ctx.bond_type in ("government", "quasi_sovereign"):
        factor *= 1.06
        notes.append("Государственное участие повышает ожидаемую поддержку.")
    return min(factor, 1.10), notes
