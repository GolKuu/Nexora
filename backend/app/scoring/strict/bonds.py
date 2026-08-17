"""Bond scoring - ``bond_score_v1``.

The organising rule of this model: **a high YTM never raises the score on its
own.** Yield is scored relative to what the credit deserves - benchmark, peers,
maturity, currency and seniority - and the resulting Yield Quality component is
hard-capped by credit quality (spec section 2). A junk issuer paying 28% earns a
*worse* yield-quality score than a solid issuer paying 14% if the 28% still does
not compensate for the risk.

Bank issuers do not touch the corporate leverage logic: their credit block is
built from the bank model in :mod:`app.scoring.strict.banks`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.scoring.strict.banks import bank_credit_components
from app.scoring.strict.base import component_map, finalise
from app.scoring.strict.caps import BOND_CAPS, bond_cap_state
from app.scoring.strict.confidence import (
    DataQualityEngine,
    FieldCheck,
    ScoreConfidenceEngine,
)
from app.scoring.strict.facts import BondFacts, derive_financials, real_return
from app.scoring.strict.pit import as_of_view
from app.scoring.strict.redflags import RedFlagEngine
from app.scoring.strict.results import StrictScore
from app.scoring.strict.scale import (
    ComponentScore,
    blend,
    cap_at,
    clamp,
    ramp,
    rating_score,
    step_high_better,
    step_low_better,
)
from app.scoring.strict.stocks import (
    cash_cover_score,
    coverage_score,
    debt_to_equity_score,
    leverage_score,
)
from app.scoring.strict.versions import BOND_MODEL

#: Spec section 2.
BOND_WEIGHTS: dict[str, float] = {
    "credit_quality": 0.30,
    "cash_flow_safety": 0.15,
    "yield_quality": 0.15,
    "real_return": 0.10,
    "liquidity": 0.10,
    "structure": 0.05,
    "rate_risk": 0.05,
    "data_quality": 0.10,
}

#: Yield Quality ceilings imposed by credit quality (spec section 2).
YIELD_QUALITY_CAPS: tuple[tuple[float, float], ...] = ((30.0, 40.0), (50.0, 60.0))


# ---------------------------------------------------------------------------
# credit quality
# ---------------------------------------------------------------------------


def _profitability(facts: BondFacts) -> float | None:
    f = facts.financials
    return blend(
        [
            (step_high_better(f.roe, [(0.20, 100.0), (0.15, 88.0), (0.10, 72.0), (0.05, 52.0),
                                      (0.0, 28.0)], worst=5.0), 0.4),
            (step_high_better(f.ebitda_margin, [(0.30, 100.0), (0.22, 88.0), (0.15, 72.0),
                                                (0.08, 52.0), (0.0, 25.0)], worst=0.0), 0.35),
            (step_high_better(f.net_margin, [(0.15, 100.0), (0.10, 85.0), (0.05, 65.0),
                                             (0.0, 40.0)], worst=5.0), 0.25),
        ]
    )


def _debt_trend(value: float | None) -> float | None:
    return step_low_better(
        value,
        [(-0.10, 100.0), (0.0, 88.0), (0.05, 72.0), (0.15, 52.0), (0.30, 28.0)],
        worst=8.0,
    )


def corporate_credit_components(facts: BondFacts) -> list[ComponentScore]:
    f = facts.financials
    leverage_raw = f.net_debt_to_ebitda if f.net_debt_to_ebitda is not None else f.debt_to_ebitda
    leverage = leverage_score(leverage_raw, f.ebitda)
    leverage_reason = None
    if f.ebitda is not None and f.ebitda <= 0:
        leverage_reason = "EBITDA отрицательна: долг нечем обслуживать из операционной прибыли."
    elif leverage_raw is not None:
        leverage_reason = f"Чистый долг {leverage_raw:.1f}x EBITDA"

    return [
        ComponentScore("leverage", "Долговая нагрузка", leverage, 0.24, leverage_raw, "x",
                       reason=leverage_reason),
        ComponentScore("interest_coverage", "Покрытие процентов",
                       coverage_score(f.interest_coverage), 0.22, f.interest_coverage, "x",
                       reason=None if f.interest_coverage is None
                       else f"Прибыль покрывает проценты {f.interest_coverage:.1f}x"),
        ComponentScore("rating", "Кредитный рейтинг", rating_score(facts.events.rating), 0.15,
                       None, None,
                       reason=None if not facts.events.rating else f"Рейтинг {facts.events.rating}"),
        ComponentScore("capital_structure", "Долг / Капитал",
                       debt_to_equity_score(f.debt_to_equity), 0.10, f.debt_to_equity, "x"),
        ComponentScore("cash_cover", "Деньги / короткий долг",
                       cash_cover_score(f.cash_to_short_term_debt), 0.09,
                       f.cash_to_short_term_debt, "x"),
        ComponentScore("profitability", "Прибыльность", _profitability(facts), 0.11, f.roe, "%"),
        ComponentScore("debt_trend", "Динамика долга", _debt_trend(f.debt_change_1y), 0.09,
                       f.debt_change_1y, "%"),
    ]


def _credit_quality(facts: BondFacts, weight: float) -> ComponentScore:
    if facts.is_bank_issuer and facts.bank_financials is not None:
        children = bank_credit_components(facts.bank_financials)
        model = "bank"
        # The agency rating still matters for a bank; it is added alongside the
        # prudential ratios rather than replacing them.
        rating = rating_score(facts.events.rating)
        if rating is not None:
            for child in children:
                child.weight *= 0.85
            children = [
                ComponentScore("rating", "Кредитный рейтинг", rating, 0.15, None, None,
                               reason=f"Рейтинг {facts.events.rating}"),
                *children,
            ]
    else:
        children = corporate_credit_components(facts)
        model = "corporate"

    score = blend([(c.score, c.weight) for c in children])

    # Events that already happened are not averaged away.
    events = facts.events
    reason = f"Модель кредитного качества: {model}."
    if score is not None:
        if events.in_default or events.missed_payment:
            score = min(score, 5.0)
            reason = "Дефолт или пропущенная выплата: кредитное качество обнулено."
        elif events.restructuring:
            score = min(score, 18.0)
            reason = "Была реструктуризация долга."
        elif events.default_history:
            score = min(score, 55.0)
            reason = "В истории эмитента был дефолт."

    return ComponentScore(
        code="credit_quality",
        label="Кредитное качество",
        score=score,
        weight=weight,
        raw_value=facts.financials.net_debt_to_ebitda,
        unit="x",
        reason=reason,
        source=facts.financials.provenance.source,
        as_of=facts.financials.provenance.as_of,
        children=children,
    )


# ---------------------------------------------------------------------------
# cash flow safety
# ---------------------------------------------------------------------------


def _cash_flow_safety(facts: BondFacts, weight: float) -> ComponentScore:
    f = facts.financials
    ocf_to_debt = None
    fcf_to_debt = None
    if f.total_debt:
        if f.operating_cash_flow is not None:
            ocf_to_debt = step_high_better(
                f.operating_cash_flow / f.total_debt,
                [(0.35, 100.0), (0.25, 90.0), (0.18, 78.0), (0.12, 62.0), (0.06, 42.0), (0.0, 22.0)],
                worst=0.0,
            )
        if f.free_cash_flow is not None:
            fcf_to_debt = step_high_better(
                f.free_cash_flow / f.total_debt,
                [(0.25, 100.0), (0.15, 88.0), (0.10, 75.0), (0.05, 60.0), (0.0, 40.0)],
                worst=10.0,
            )
    if ocf_to_debt is None and f.operating_cash_flow is not None:
        ocf_to_debt = 75.0 if f.operating_cash_flow > 0 else 15.0
    if fcf_to_debt is None and f.free_cash_flow is not None:
        fcf_to_debt = 70.0 if f.free_cash_flow > 0 else 20.0

    ocf_to_interest = None
    if f.operating_cash_flow is not None and f.interest_expense:
        ocf_to_interest = step_high_better(
            f.operating_cash_flow / abs(f.interest_expense),
            [(8.0, 100.0), (5.0, 88.0), (3.0, 72.0), (2.0, 55.0), (1.0, 32.0)],
            worst=8.0,
        )
    conversion = step_high_better(
        f.cash_conversion,
        [(0.90, 100.0), (0.75, 88.0), (0.60, 72.0), (0.40, 52.0), (0.20, 32.0)],
        worst=10.0,
    )

    children = [
        ComponentScore("ocf_to_debt", "Операционный поток к долгу", ocf_to_debt, 0.30,
                       f.operating_cash_flow, facts.currency),
        ComponentScore("fcf_to_debt", "Свободный поток к долгу", fcf_to_debt, 0.25,
                       f.free_cash_flow, facts.currency),
        ComponentScore("ocf_to_interest", "Поток к процентным платежам", ocf_to_interest, 0.25,
                       f.interest_expense, facts.currency),
        ComponentScore("cash_conversion", "Конвертация EBITDA в деньги", conversion, 0.20,
                       f.cash_conversion, "x"),
    ]
    return ComponentScore(
        code="cash_flow_safety",
        label="Безопасность денежного потока",
        score=blend([(c.score, c.weight) for c in children]),
        weight=weight,
        raw_value=f.free_cash_flow,
        unit=facts.currency,
        reason="Хватает ли реальных денег на обслуживание долга.",
        source=f.provenance.source,
        as_of=f.provenance.as_of,
        children=children,
    )


# ---------------------------------------------------------------------------
# yield quality
# ---------------------------------------------------------------------------


def required_spread(credit_quality: float | None) -> float | None:
    """Spread a credit of this quality must pay before the yield is 'fair'.

    Convex, like a real credit curve: ~30 bp for a flawless credit, ~100 bp
    around investment grade, ~5 pp at the crossover, ~20 pp for a distressed
    issuer. This is the number that decides whether a fat coupon is compensation
    or a warning, so it is stated in one place and versioned with the model.
    """
    if credit_quality is None:
        return None
    shortfall = (100.0 - clamp(credit_quality)) / 100.0
    return 0.003 + shortfall * shortfall * 0.30


def _yield_quality(facts: BondFacts, credit_quality: float | None, weight: float) -> ComponentScore:
    ytm = facts.ytm
    benchmark = facts.macro.benchmark_yield
    children: list[ComponentScore] = []
    reasons: list[str] = []

    vs_benchmark = None
    excess = None
    if ytm is not None and benchmark is not None:
        needed = required_spread(credit_quality)
        spread = ytm - benchmark
        if needed is not None:
            excess = spread - needed
            vs_benchmark = ramp(
                excess,
                [(-0.06, 0.0), (-0.03, 15.0), (-0.01, 35.0), (0.0, 55.0), (0.01, 70.0),
                 (0.025, 85.0), (0.05, 95.0), (0.08, 100.0)],
            )
            reasons.append(
                f"Премия к бенчмарку {spread * 100:.1f} п.п. при требуемых "
                f"{needed * 100:.1f} п.п. за такой кредитный риск"
            )
    children.append(
        ComponentScore("vs_benchmark", "Премия сверх требуемой за риск", vs_benchmark, 0.65,
                       excess, "п.п.")
    )

    vs_peers = None
    if ytm is not None and facts.peers.peer_median_ytm is not None and facts.peers.peer_count >= 2:
        vs_peers = ramp(
            ytm - facts.peers.peer_median_ytm,
            [(-0.03, 15.0), (-0.01, 35.0), (0.0, 55.0), (0.01, 70.0), (0.03, 88.0), (0.05, 100.0)],
        )
    children.append(
        ComponentScore("vs_peers", "Против похожих выпусков", vs_peers, 0.35,
                       facts.peers.peer_median_ytm, "%")
    )

    score = blend([(c.score, c.weight) for c in children])

    if score is not None:
        # Long maturities and weak credit compound: the same spread buys less
        # comfort over ten years than over one.
        if facts.years_to_maturity is not None and facts.years_to_maturity > 7 and (
            credit_quality is not None and credit_quality < 60
        ):
            score *= 0.90
            reasons.append("длинный срок при среднем кредитном качестве")
        if facts.subordinated:
            score *= 0.92
            reasons.append("субординированный выпуск")
        elif facts.secured:
            score = min(score * 1.03, 100.0)
        if facts.currency != "KZT" and facts.macro.benchmark_yield is None:
            score *= 0.95
            reasons.append("нет бенчмарка в валюте выпуска")

    # Spec section 2: weak credit caps how good a yield is allowed to look.
    capped_reason = None
    if score is not None and credit_quality is not None:
        for credit_threshold, ceiling in YIELD_QUALITY_CAPS:
            if credit_quality < credit_threshold and score > ceiling:
                score = cap_at(score, ceiling)
                capped_reason = (
                    f"Кредитное качество {credit_quality:.0f} (<{credit_threshold:.0f}): "
                    f"качество доходности ограничено {ceiling:.0f}."
                )
                break

    reason = capped_reason or ("; ".join(reasons) + "." if reasons else
                               "Недостаточно данных для сравнения доходности.")
    return ComponentScore(
        code="yield_quality",
        label="Качество доходности",
        score=None if score is None else clamp(score),
        weight=weight,
        raw_value=ytm,
        unit="YTM",
        reason=reason,
        source=facts.market.provenance.source,
        as_of=facts.market.provenance.as_of,
        children=children,
    )


# ---------------------------------------------------------------------------
# real return, liquidity, structure, rate risk
# ---------------------------------------------------------------------------


def _real_return(facts: BondFacts, weight: float) -> ComponentScore:
    value = real_return(facts.ytm, facts.macro.inflation_rate)
    score = step_high_better(
        value,
        [(0.06, 100.0), (0.04, 90.0), (0.02, 78.0), (0.005, 62.0), (0.0, 50.0),
         (-0.02, 30.0), (-0.05, 12.0)],
        worst=0.0,
    )
    reason = None
    if value is not None:
        reason = (
            f"Реальная доходность {value * 100:.1f}% при инфляции "
            f"{(facts.macro.inflation_rate or 0) * 100:.1f}%"
        )
        if value < 0:
            reason += " — покупательная способность снижается."
    return ComponentScore(
        code="real_return",
        label="Доход после инфляции",
        score=score,
        weight=weight,
        raw_value=value,
        unit="%",
        reason=reason,
        source=facts.macro.provenance.source,
        as_of=facts.macro.provenance.as_of,
    )


def _liquidity(facts: BondFacts, weight: float) -> ComponentScore:
    m = facts.market
    spread = m.derived_spread_pct()
    quotes = None
    if m.bid is not None or m.ask is not None:
        quotes = 100.0 if (m.bid and m.ask) else 40.0
    elif m.price is not None:
        quotes = 0.0

    children = [
        ComponentScore("spread", "Спред покупки и продажи",
                       step_low_better(spread, [(0.002, 100.0), (0.005, 88.0), (0.01, 75.0),
                                                (0.02, 60.0), (0.035, 42.0), (0.06, 22.0)], worst=5.0),
                       0.25, spread, "%"),
        ComponentScore("turnover", "Среднедневной оборот",
                       step_high_better(m.avg_daily_turnover,
                                        [(2e8, 100.0), (5e7, 88.0), (1e7, 72.0), (3e6, 55.0),
                                         (1e6, 38.0), (2e5, 20.0)], worst=5.0),
                       0.25, m.avg_daily_turnover, facts.currency),
        ComponentScore("trade_count", "Количество сделок за месяц",
                       step_high_better(m.trade_count_30d,
                                        [(20.0, 100.0), (12.0, 88.0), (8.0, 75.0), (4.0, 58.0),
                                         (2.0, 38.0), (1.0, 20.0)], worst=5.0),
                       0.20, m.trade_count_30d, "сделок"),
        ComponentScore("staleness", "Давность последней сделки",
                       step_low_better(m.days_since_last_trade,
                                       [(1.0, 100.0), (3.0, 88.0), (7.0, 72.0), (15.0, 50.0),
                                        (30.0, 28.0), (60.0, 12.0)], worst=2.0),
                       0.15, m.days_since_last_trade, "дней"),
        ComponentScore("depth", "Глубина стакана",
                       step_high_better(m.order_book_depth,
                                        [(1e8, 100.0), (3e7, 85.0), (1e7, 70.0), (3e6, 50.0),
                                         (1e6, 30.0)], worst=10.0),
                       0.10, m.order_book_depth, facts.currency),
        ComponentScore("quotes", "Наличие котировок", quotes, 0.05, None, None),
    ]
    return ComponentScore(
        code="liquidity",
        label="Ликвидность",
        score=blend([(c.score, c.weight) for c in children]),
        weight=weight,
        raw_value=m.avg_daily_turnover,
        unit=facts.currency,
        reason="Насколько реально выйти из позиции по разумной цене.",
        source=m.provenance.source,
        as_of=m.provenance.as_of,
        children=children,
    )


def _structure(facts: BondFacts, weight: float) -> ComponentScore:
    seniority = None
    if facts.subordinated is not None or facts.secured is not None:
        if facts.subordinated:
            seniority = 30.0
        elif facts.secured:
            seniority = 100.0
        else:
            seniority = 80.0
    covenants = {"strong": 100.0, "standard": 70.0, "weak": 40.0, "none": 25.0}.get(
        (facts.covenants or "").lower()
    )
    optionality = None
    if facts.callable is not None or facts.amortizing is not None:
        if facts.callable:
            optionality = 50.0
        elif facts.amortizing:
            optionality = 85.0
        else:
            optionality = 90.0
    certainty = {
        "fixed": 100.0, "step": 85.0, "indexed": 75.0, "floating": 65.0, "zero": 50.0,
    }.get((facts.coupon_type or "").lower())

    children = [
        ComponentScore("seniority", "Очередность выплат", seniority, 0.35),
        ComponentScore("coupon_certainty", "Предсказуемость купона", certainty, 0.25),
        ComponentScore("optionality", "Досрочный выкуп и амортизация", optionality, 0.20),
        ComponentScore("covenants", "Ковенанты", covenants, 0.20),
    ]
    return ComponentScore(
        code="structure",
        label="Структура выпуска",
        score=blend([(c.score, c.weight) for c in children]),
        weight=weight,
        reason="Очередность, ковенанты и опции эмитента влияют на возврат средств.",
        children=children,
    )


def _rate_risk(facts: BondFacts, weight: float) -> ComponentScore:
    duration = facts.modified_duration
    if duration is None and facts.years_to_maturity is not None and facts.coupon_rate:
        # Rough fallback so a missing analytic does not silently drop the block.
        duration = facts.years_to_maturity / (1.0 + facts.coupon_rate * facts.years_to_maturity / 2.0)
    score = step_low_better(
        duration,
        [(1.0, 100.0), (2.0, 90.0), (3.0, 80.0), (5.0, 65.0), (7.0, 48.0), (10.0, 28.0)],
        worst=12.0,
    )
    outlook = (facts.macro.rate_outlook or "").lower()
    reason = None if duration is None else f"Дюрация {duration:.1f} года"
    if score is not None and outlook == "rising":
        score *= 0.85
        reason = (reason or "") + "; ставки ожидаются выше — цена длинных бумаг под давлением"
    elif score is not None and outlook == "falling":
        score = min(score * 1.10, 100.0)
        reason = (reason or "") + "; ожидаемое снижение ставок поддерживает цену"
    if (facts.coupon_type or "").lower() == "floating" and score is not None:
        score = max(score, 85.0)
        reason = (reason or "") + "; плавающий купон снижает процентный риск"
    return ComponentScore(
        code="rate_risk",
        label="Процентный риск",
        score=score,
        weight=weight,
        raw_value=duration,
        unit="лет",
        reason=reason,
    )


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class BondScoringEngine:
    """``bond_score_v1``."""

    version = BOND_MODEL

    def __init__(self) -> None:
        self.flags = RedFlagEngine()
        self.data_quality = DataQualityEngine()
        self.confidence = ScoreConfidenceEngine()

    def _checks(self, facts: BondFacts) -> list[FieldCheck]:
        f = facts.financials
        bank = facts.bank_financials
        if facts.is_bank_issuer and bank is not None:
            credit_checks = [
                FieldCheck("car", "достаточность капитала",
                           bank.capital_adequacy_ratio is not None, critical=True),
                FieldCheck("npl", "качество кредитного портфеля", bank.npl_ratio is not None,
                           critical=True),
                FieldCheck("bank_liquidity", "ликвидные активы банка",
                           bank.liquid_assets_ratio is not None),
            ]
        else:
            leverage = f.net_debt_to_ebitda if f.net_debt_to_ebitda is not None else f.debt_to_ebitda
            credit_checks = [
                FieldCheck("leverage", "долговая нагрузка", leverage is not None, critical=True),
                FieldCheck("coverage", "покрытие процентов", f.interest_coverage is not None,
                           critical=True),
                FieldCheck("cash_flow", "денежный поток",
                           f.operating_cash_flow is not None or f.free_cash_flow is not None,
                           critical=True),
                FieldCheck("equity", "собственный капитал",
                           f.equity is not None or f.debt_to_equity is not None),
            ]
        return [
            *credit_checks,
            FieldCheck("ytm", "доходность к погашению", facts.ytm is not None, critical=True),
            FieldCheck("rating", "кредитный рейтинг", facts.events.rating is not None),
            FieldCheck("benchmark", "бенчмарк доходности",
                       facts.macro.benchmark_yield is not None, critical=True),
            FieldCheck("inflation", "инфляция", facts.macro.inflation_rate is not None),
            FieldCheck("quotes", "котировки bid/ask",
                       facts.market.bid is not None and facts.market.ask is not None),
            FieldCheck("turnover", "объем торгов", facts.market.avg_daily_turnover is not None),
            FieldCheck("maturity", "срок до погашения", facts.years_to_maturity is not None),
        ]

    def score(self, facts: BondFacts, *, as_of: datetime | None = None) -> StrictScore:
        view = as_of_view(facts, as_of)
        f: BondFacts = view.facts
        derive_financials(f.financials)
        now = datetime.now(timezone.utc)
        moment = view.as_of or now

        dq_input = self.data_quality.evaluate(f, self._checks(f), moment=moment)
        credit = _credit_quality(f, BOND_WEIGHTS["credit_quality"])
        liquidity = _liquidity(f, BOND_WEIGHTS["liquidity"])

        components = [
            credit,
            _cash_flow_safety(f, BOND_WEIGHTS["cash_flow_safety"]),
            _yield_quality(f, credit.score, BOND_WEIGHTS["yield_quality"]),
            _real_return(f, BOND_WEIGHTS["real_return"]),
            liquidity,
            _structure(f, BOND_WEIGHTS["structure"]),
            _rate_risk(f, BOND_WEIGHTS["rate_risk"]),
            ComponentScore("data_quality", "Качество данных", dq_input.value,
                           BOND_WEIGHTS["data_quality"],
                           reason="Полнота, свежесть и официальность источников."),
        ]

        flags = self.flags.for_bond(
            f,
            missing_critical=dq_input.missing_critical,
            real_return=real_return(f.ytm, f.macro.inflation_rate),
        )
        confidence = self.confidence.evaluate(f, data_quality=dq_input, liquidity_score=liquidity.score)
        state = bond_cap_state(f, component_map(components), flags)

        return finalise(
            kind="bond",
            ticker=f.ticker,
            model=self.version,
            components=components,
            flags=flags,
            cap_rules=BOND_CAPS,
            cap_state=state,
            data_quality=dq_input,
            confidence=confidence,
            as_of=view.as_of,
            excluded_facts=view.excluded,
            notes=[],
            now=now,
        )
