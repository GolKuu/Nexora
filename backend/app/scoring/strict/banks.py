"""Bank scoring - a genuinely separate model, not a corporate model with excuses.

A bank funds itself with deposits; leverage *is* the business. Running
Debt/EBITDA over a bank produces a confident-looking number with no meaning, so
none of the corporate leverage logic is reachable from here. Capital adequacy,
asset quality and funding replace it.

Model: ``bank_score_v1``
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.scoring.strict.base import component_map, finalise
from app.scoring.strict.caps import BANK_CAPS, bank_cap_state
from app.scoring.strict.confidence import (
    DataQualityEngine,
    FieldCheck,
    ScoreConfidenceEngine,
)
from app.scoring.strict.facts import BankFinancials, StockFacts
from app.scoring.strict.pit import as_of_view
from app.scoring.strict.redflags import RedFlagEngine
from app.scoring.strict.results import StrictScore
from app.scoring.strict.scale import (
    ComponentScore,
    blend,
    cap_at,
    clamp,
    ramp,
    step_high_better,
    step_low_better,
)
from app.scoring.strict.versions import BANK_MODEL

#: Spec section 7.
BANK_WEIGHTS: dict[str, float] = {
    "profitability": 0.20,
    "capital_strength": 0.20,
    "asset_quality": 0.20,
    "funding_liquidity": 0.15,
    "efficiency": 0.10,
    "valuation": 0.10,
    "data_quality": 0.05,
}


# ---------------------------------------------------------------------------
# metric bands
# ---------------------------------------------------------------------------


def _roe(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.25, 100.0), (0.18, 92.0), (0.14, 82.0), (0.10, 70.0), (0.06, 52.0),
         (0.02, 32.0), (0.0, 18.0)],
        worst=0.0,
    )


def _roa(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.035, 100.0), (0.025, 90.0), (0.018, 78.0), (0.012, 62.0), (0.006, 42.0), (0.0, 22.0)],
        worst=0.0,
    )


def _nim(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.06, 100.0), (0.045, 88.0), (0.035, 72.0), (0.025, 55.0), (0.015, 35.0)],
        worst=15.0,
    )


def _car(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.20, 100.0), (0.17, 92.0), (0.15, 82.0), (0.13, 68.0), (0.115, 50.0),
         (0.10, 30.0), (0.08, 12.0)],
        worst=0.0,
    )


def _tier1(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.16, 100.0), (0.13, 88.0), (0.11, 72.0), (0.09, 50.0), (0.07, 25.0)],
        worst=5.0,
    )


def _equity_to_assets(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.15, 100.0), (0.12, 90.0), (0.10, 78.0), (0.08, 62.0), (0.06, 42.0), (0.04, 22.0)],
        worst=5.0,
    )


def _npl(value: float | None) -> float | None:
    return step_low_better(
        value,
        [(0.02, 100.0), (0.04, 88.0), (0.06, 72.0), (0.08, 55.0), (0.12, 35.0), (0.20, 15.0)],
        worst=0.0,
    )


def _npl_coverage(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(1.2, 100.0), (1.0, 90.0), (0.8, 75.0), (0.6, 55.0), (0.4, 30.0)],
        worst=10.0,
    )


def _cost_of_risk(value: float | None) -> float | None:
    return step_low_better(
        value,
        [(0.005, 100.0), (0.01, 88.0), (0.02, 70.0), (0.03, 50.0), (0.05, 25.0)],
        worst=5.0,
    )


def _loan_to_deposit(value: float | None) -> float | None:
    # Both extremes are a warning: too low means the book is not lending, too
    # high means the bank is funding loans on the wholesale market.
    return ramp(
        value,
        [(0.3, 50.0), (0.6, 85.0), (0.8, 100.0), (1.0, 85.0), (1.15, 62.0),
         (1.3, 38.0), (1.6, 10.0)],
    )


def _deposit_growth(value: float | None) -> float | None:
    # Rapid deposit growth is not automatically good - it is often bought with
    # rates the bank cannot earn back.
    return ramp(
        value,
        [(-0.20, 5.0), (-0.05, 35.0), (0.0, 55.0), (0.10, 85.0), (0.25, 100.0),
         (0.50, 75.0), (0.80, 45.0)],
    )


def _liquid_assets(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.30, 100.0), (0.22, 88.0), (0.16, 72.0), (0.12, 55.0), (0.08, 35.0)],
        worst=12.0,
    )


def _cost_to_income(value: float | None) -> float | None:
    return step_low_better(
        value,
        [(0.35, 100.0), (0.45, 88.0), (0.55, 70.0), (0.65, 52.0), (0.75, 32.0), (0.85, 15.0)],
        worst=5.0,
    )


# ---------------------------------------------------------------------------
# component builders (reused by the bond engine for bank issuers)
# ---------------------------------------------------------------------------


def profitability_component(bank: BankFinancials, weight: float) -> ComponentScore:
    score = blend([(_roe(bank.roe), 0.40), (_roa(bank.roa), 0.35), (_nim(bank.net_interest_margin), 0.25)])
    return ComponentScore(
        code="profitability",
        label="Прибыльность банка",
        score=score,
        weight=weight,
        raw_value=bank.roe,
        unit="ROE",
        reason=None if bank.roe is None else f"ROE {bank.roe * 100:.1f}%, ROA {(bank.roa or 0) * 100:.2f}%",
        source=bank.provenance.source,
        as_of=bank.provenance.as_of,
        children=[
            ComponentScore("roe", "ROE", _roe(bank.roe), 0.40, bank.roe, "%"),
            ComponentScore("roa", "ROA", _roa(bank.roa), 0.35, bank.roa, "%"),
            ComponentScore("nim", "Чистая процентная маржа", _nim(bank.net_interest_margin),
                           0.25, bank.net_interest_margin, "%"),
        ],
    )


def capital_component(bank: BankFinancials, weight: float) -> ComponentScore:
    score = blend(
        [
            (_car(bank.capital_adequacy_ratio), 0.45),
            (_tier1(bank.tier1_ratio), 0.30),
            (_equity_to_assets(bank.equity_to_assets), 0.25),
        ]
    )
    return ComponentScore(
        code="capital_strength",
        label="Достаточность капитала",
        score=score,
        weight=weight,
        raw_value=bank.capital_adequacy_ratio,
        unit="%",
        reason=None if bank.capital_adequacy_ratio is None
        else f"Достаточность капитала {bank.capital_adequacy_ratio * 100:.1f}%",
        source=bank.provenance.source,
        as_of=bank.provenance.as_of,
        children=[
            ComponentScore("car", "Коэффициент достаточности капитала",
                           _car(bank.capital_adequacy_ratio), 0.45, bank.capital_adequacy_ratio, "%"),
            ComponentScore("tier1", "Капитал 1 уровня", _tier1(bank.tier1_ratio), 0.30,
                           bank.tier1_ratio, "%"),
            ComponentScore("equity_to_assets", "Капитал к активам",
                           _equity_to_assets(bank.equity_to_assets), 0.25, bank.equity_to_assets, "%"),
        ],
    )


def asset_quality_component(bank: BankFinancials, weight: float) -> ComponentScore:
    score = blend(
        [
            (_npl(bank.npl_ratio), 0.45),
            (_npl_coverage(bank.npl_coverage), 0.30),
            (_cost_of_risk(bank.cost_of_risk), 0.25),
        ]
    )
    return ComponentScore(
        code="asset_quality",
        label="Качество активов",
        score=score,
        weight=weight,
        raw_value=bank.npl_ratio,
        unit="%",
        reason=None if bank.npl_ratio is None
        else f"Проблемные кредиты {bank.npl_ratio * 100:.1f}%, покрытие резервами "
             f"{(bank.npl_coverage or 0) * 100:.0f}%",
        source=bank.provenance.source,
        as_of=bank.provenance.as_of,
        children=[
            ComponentScore("npl", "Доля проблемных кредитов", _npl(bank.npl_ratio), 0.45,
                           bank.npl_ratio, "%"),
            ComponentScore("npl_coverage", "Покрытие резервами", _npl_coverage(bank.npl_coverage),
                           0.30, bank.npl_coverage, "x"),
            ComponentScore("cost_of_risk", "Стоимость риска", _cost_of_risk(bank.cost_of_risk),
                           0.25, bank.cost_of_risk, "%"),
        ],
    )


def funding_component(bank: BankFinancials, weight: float) -> ComponentScore:
    score = blend(
        [
            (_loan_to_deposit(bank.loan_to_deposit), 0.40),
            (_liquid_assets(bank.liquid_assets_ratio), 0.35),
            (_deposit_growth(bank.deposit_growth), 0.25),
        ]
    )
    return ComponentScore(
        code="funding_liquidity",
        label="Фондирование и ликвидность",
        score=score,
        weight=weight,
        raw_value=bank.loan_to_deposit,
        unit="x",
        reason=None if bank.loan_to_deposit is None
        else f"Кредиты/депозиты {bank.loan_to_deposit:.2f}x",
        source=bank.provenance.source,
        as_of=bank.provenance.as_of,
        children=[
            ComponentScore("loan_to_deposit", "Кредиты к депозитам",
                           _loan_to_deposit(bank.loan_to_deposit), 0.40, bank.loan_to_deposit, "x"),
            ComponentScore("liquid_assets", "Ликвидные активы",
                           _liquid_assets(bank.liquid_assets_ratio), 0.35, bank.liquid_assets_ratio, "%"),
            ComponentScore("deposit_growth", "Динамика депозитов",
                           _deposit_growth(bank.deposit_growth), 0.25, bank.deposit_growth, "%"),
        ],
    )


def efficiency_component(bank: BankFinancials, weight: float) -> ComponentScore:
    score = _cost_to_income(bank.cost_to_income)
    return ComponentScore(
        code="efficiency",
        label="Операционная эффективность",
        score=score,
        weight=weight,
        raw_value=bank.cost_to_income,
        unit="%",
        reason=None if bank.cost_to_income is None
        else f"Расходы/доходы {bank.cost_to_income * 100:.0f}%",
        source=bank.provenance.source,
        as_of=bank.provenance.as_of,
    )


def bank_credit_components(bank: BankFinancials) -> list[ComponentScore]:
    """Sub-components used when a *bond* is issued by a bank.

    Same metrics, re-weighted for a creditor's point of view: capital and asset
    quality matter more than the return earned on that capital.
    """
    return [
        capital_component(bank, 0.34),
        asset_quality_component(bank, 0.30),
        funding_component(bank, 0.22),
        profitability_component(bank, 0.10),
        efficiency_component(bank, 0.04),
    ]


# ---------------------------------------------------------------------------
# valuation
# ---------------------------------------------------------------------------


def _valuation_component(facts: StockFacts, quality_floor: float | None, weight: float) -> ComponentScore:
    pb = step_low_better(
        facts.pb, [(0.6, 95.0), (0.9, 88.0), (1.1, 76.0), (1.4, 60.0), (1.8, 42.0), (2.5, 22.0)],
        worst=8.0,
    )
    pe = step_low_better(
        facts.pe, [(6.0, 95.0), (8.0, 88.0), (11.0, 75.0), (14.0, 60.0), (18.0, 42.0), (25.0, 22.0)],
        worst=8.0,
    )
    dividend = step_high_better(
        facts.dividend_yield,
        [(0.10, 100.0), (0.07, 88.0), (0.05, 74.0), (0.03, 56.0), (0.01, 36.0)],
        worst=20.0,
    )
    score = blend([(pb, 0.40), (pe, 0.35), (dividend, 0.25)])

    reason = None
    # A bank trading below book is only cheap if the book is real. Weak capital
    # or a bad loan portfolio caps how attractive the valuation may look.
    if score is not None and quality_floor is not None:
        if quality_floor < 30:
            score = cap_at(score, 40.0)
            reason = "Дешевая оценка не учитывается: капитал и качество активов слабые."
        elif quality_floor < 50:
            score = cap_at(score, 60.0)
            reason = "Оценка ограничена: качество баланса ниже среднего."
    return ComponentScore(
        code="valuation",
        label="Оценка рынком",
        score=score,
        weight=weight,
        raw_value=facts.pb,
        unit="P/B",
        reason=reason or (None if facts.pb is None else f"P/B {facts.pb:.2f}"),
        source=facts.market.provenance.source,
        as_of=facts.market.provenance.as_of,
        children=[
            ComponentScore("pb", "P/B", pb, 0.40, facts.pb, "x"),
            ComponentScore("pe", "P/E", pe, 0.35, facts.pe, "x"),
            ComponentScore("dividend_yield", "Дивидендная доходность", dividend, 0.25,
                           facts.dividend_yield, "%"),
        ],
    )


def market_liquidity_score(facts: StockFacts) -> float | None:
    """Shared with the stock engine; kept here to avoid a circular import."""
    from app.scoring.strict.stocks import liquidity_score

    return liquidity_score(facts)


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class BankScoringEngine:
    """``bank_score_v1`` - equity scoring for banks and other deposit takers."""

    version = BANK_MODEL

    def __init__(self) -> None:
        self.flags = RedFlagEngine()
        self.data_quality = DataQualityEngine()
        self.confidence = ScoreConfidenceEngine()

    def _checks(self, bank: BankFinancials, facts: StockFacts) -> list[FieldCheck]:
        return [
            FieldCheck("car", "достаточность капитала", bank.capital_adequacy_ratio is not None, critical=True),
            FieldCheck("npl", "доля проблемных кредитов", bank.npl_ratio is not None, critical=True),
            FieldCheck("npl_coverage", "покрытие резервами", bank.npl_coverage is not None),
            FieldCheck("roe", "ROE", bank.roe is not None, critical=True),
            FieldCheck("nim", "процентная маржа", bank.net_interest_margin is not None),
            FieldCheck("ltd", "кредиты/депозиты", bank.loan_to_deposit is not None),
            FieldCheck("cost_to_income", "расходы/доходы", bank.cost_to_income is not None),
            FieldCheck("valuation", "рыночная оценка", facts.pb is not None or facts.pe is not None,
                       critical=True),
            FieldCheck("quotes", "котировки", facts.market.bid is not None and facts.market.ask is not None),
            FieldCheck("turnover", "объем торгов", facts.market.avg_daily_turnover is not None),
        ]

    def score(self, facts: StockFacts, *, as_of: datetime | None = None) -> StrictScore:
        view = as_of_view(facts, as_of)
        f: StockFacts = view.facts
        bank = f.bank_financials or BankFinancials()
        now = datetime.now(timezone.utc)
        moment = view.as_of or now

        capital = capital_component(bank, BANK_WEIGHTS["capital_strength"])
        asset_quality = asset_quality_component(bank, BANK_WEIGHTS["asset_quality"])
        quality_floor = blend([(capital.score, 0.5), (asset_quality.score, 0.5)])

        dq_input = self.data_quality.evaluate(f, self._checks(bank, f), moment=moment)
        liquidity = market_liquidity_score(f)

        components = [
            profitability_component(bank, BANK_WEIGHTS["profitability"]),
            capital,
            asset_quality,
            funding_component(bank, BANK_WEIGHTS["funding_liquidity"]),
            efficiency_component(bank, BANK_WEIGHTS["efficiency"]),
            _valuation_component(f, quality_floor, BANK_WEIGHTS["valuation"]),
            ComponentScore("data_quality", "Качество данных", dq_input.value,
                           BANK_WEIGHTS["data_quality"],
                           reason="Полнота, свежесть и официальность источников."),
            # Not weighted: the spec fixes the bank weights. Market liquidity
            # still reaches the final score through its red flag and its cap.
            ComponentScore("market_liquidity", "Ликвидность на бирже", liquidity, 0.0,
                           raw_value=f.market.avg_daily_turnover, unit=f.currency),
        ]

        flags = self.flags.for_stock(f, missing_critical=dq_input.missing_critical)
        confidence = self.confidence.evaluate(f, data_quality=dq_input, liquidity_score=liquidity)
        state = bank_cap_state(f, component_map(components), flags)

        return finalise(
            kind="bank",
            ticker=f.ticker,
            model=self.version,
            components=components,
            flags=flags,
            cap_rules=BANK_CAPS,
            cap_state=state,
            data_quality=dq_input,
            confidence=confidence,
            as_of=view.as_of,
            excluded_facts=view.excluded,
            notes=[],
            now=now,
        )


def clamp_score(value: float | None) -> float | None:
    return None if value is None else clamp(value)
