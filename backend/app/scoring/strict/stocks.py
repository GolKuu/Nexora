"""Equity scoring - ``stock_score_v1``.

Two rules shape this model more than any other:

* a low P/E is not an argument on its own. Cheapness is only credited once the
  business is shown to be intact; when earnings, cash flow and revenue are all
  falling while debt rises, the valuation component is explicitly discounted as
  a value trap.
* a high ROE built on borrowed money is not quality. ROE is scaled down as
  Debt/Equity rises, so leverage cannot buy a business-quality score.

Banks never reach this module's balance-sheet logic - they route to
:mod:`app.scoring.strict.banks`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.scoring.strict.base import component_map, finalise
from app.scoring.strict.caps import STOCK_CAPS, stock_cap_state
from app.scoring.strict.confidence import (
    DataQualityEngine,
    FieldCheck,
    ScoreConfidenceEngine,
)
from app.scoring.strict.facts import StockFacts, derive_financials
from app.scoring.strict.pit import as_of_view
from app.scoring.strict.redflags import RedFlagEngine
from app.scoring.strict.results import StrictScore
from app.scoring.strict.scale import (
    ComponentScore,
    blend,
    clamp,
    ramp,
    step_high_better,
    step_low_better,
)
from app.scoring.strict.versions import STOCK_MODEL

#: Spec section 5.
STOCK_WEIGHTS: dict[str, float] = {
    "business_quality": 0.20,
    "growth": 0.15,
    "valuation": 0.20,
    "financial_strength": 0.15,
    "shareholder_return": 0.10,
    "liquidity": 0.10,
    "risk_stability": 0.05,
    "data_quality": 0.05,
}


# ---------------------------------------------------------------------------
# metric bands
# ---------------------------------------------------------------------------


def _cagr(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.25, 100.0), (0.15, 88.0), (0.10, 75.0), (0.05, 60.0), (0.02, 48.0),
         (0.0, 38.0), (-0.05, 22.0)],
        worst=8.0,
    )


def _roic(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.20, 100.0), (0.15, 90.0), (0.12, 80.0), (0.09, 65.0), (0.06, 48.0), (0.03, 28.0)],
        worst=10.0,
    )


def _roe_raw(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.25, 100.0), (0.20, 92.0), (0.15, 82.0), (0.11, 68.0), (0.07, 50.0),
         (0.03, 30.0), (0.0, 15.0)],
        worst=0.0,
    )


def _roa(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.12, 100.0), (0.09, 88.0), (0.06, 72.0), (0.04, 55.0), (0.02, 35.0), (0.0, 18.0)],
        worst=0.0,
    )


def _margin(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.25, 100.0), (0.18, 88.0), (0.12, 74.0), (0.08, 58.0), (0.04, 40.0), (0.0, 20.0)],
        worst=0.0,
    )


def _fcf_margin(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.15, 100.0), (0.10, 88.0), (0.06, 72.0), (0.03, 55.0), (0.0, 35.0)],
        worst=8.0,
    )


def _cash_conversion(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(0.90, 100.0), (0.75, 88.0), (0.60, 72.0), (0.40, 52.0), (0.20, 32.0)],
        worst=10.0,
    )


def _fraction(value: float | None) -> float | None:
    return None if value is None else clamp(value * 100.0)


def _dilution(value: float | None) -> float | None:
    """Share-count growth. Buybacks (negative) score full marks."""
    return step_low_better(
        value,
        [(0.0, 100.0), (0.01, 88.0), (0.03, 72.0), (0.06, 52.0), (0.10, 32.0), (0.20, 12.0)],
        worst=0.0,
    )


def leverage_score(net_debt_to_ebitda: float | None, ebitda: float | None) -> float | None:
    """Spec section 2 bands, with negative EBITDA scoring zero outright."""
    if ebitda is not None and ebitda <= 0:
        return 0.0
    return step_low_better(
        net_debt_to_ebitda,
        [(1.0, 100.0), (2.0, 90.0), (3.0, 75.0), (4.0, 55.0), (5.0, 35.0)],
        worst=15.0,
    )


def coverage_score(interest_coverage: float | None) -> float | None:
    """Spec section 2: >8x = 100 ... 1-2x ramps 15-35 ... <1x = 0."""
    if interest_coverage is None:
        return None
    if interest_coverage < 1.0:
        return 0.0
    if interest_coverage < 2.0:
        return ramp(interest_coverage, [(1.0, 15.0), (2.0, 35.0)])
    if interest_coverage < 3.0:
        return 55.0
    if interest_coverage < 5.0:
        return 75.0
    if interest_coverage <= 8.0:
        return 90.0
    return 100.0


def debt_to_equity_score(value: float | None) -> float | None:
    return step_low_better(
        value,
        [(0.3, 100.0), (0.6, 90.0), (1.0, 78.0), (1.5, 62.0), (2.0, 45.0), (3.0, 25.0)],
        worst=8.0,
    )


def cash_cover_score(value: float | None) -> float | None:
    return step_high_better(
        value,
        [(2.0, 100.0), (1.5, 90.0), (1.0, 78.0), (0.7, 60.0), (0.4, 40.0), (0.2, 22.0)],
        worst=8.0,
    )


# ---------------------------------------------------------------------------
# liquidity (shared with the bank engine)
# ---------------------------------------------------------------------------


def _liquidity_children(facts: StockFacts) -> list[ComponentScore]:
    m = facts.market
    spread = m.derived_spread_pct()
    quotes = None
    if m.bid is not None or m.ask is not None:
        quotes = 100.0 if (m.bid and m.ask) else 45.0
    elif m.price is not None:
        quotes = 0.0
    return [
        ComponentScore(
            "spread", "Спред покупки и продажи",
            step_low_better(spread, [(0.002, 100.0), (0.005, 88.0), (0.01, 75.0), (0.02, 60.0),
                                     (0.035, 42.0), (0.06, 22.0)], worst=5.0),
            0.25, spread, "%",
        ),
        ComponentScore(
            "turnover", "Среднедневной оборот",
            step_high_better(m.avg_daily_turnover,
                             [(2e8, 100.0), (5e7, 88.0), (1e7, 72.0), (3e6, 55.0),
                              (1e6, 38.0), (2e5, 20.0)], worst=5.0),
            0.25, m.avg_daily_turnover, facts.currency,
        ),
        ComponentScore(
            "trade_count", "Количество сделок за месяц",
            step_high_better(m.trade_count_30d,
                             [(20.0, 100.0), (12.0, 88.0), (8.0, 75.0), (4.0, 58.0),
                              (2.0, 38.0), (1.0, 20.0)], worst=5.0),
            0.20, m.trade_count_30d, "сделок",
        ),
        ComponentScore(
            "staleness", "Давность последней сделки",
            step_low_better(m.days_since_last_trade,
                            [(1.0, 100.0), (3.0, 88.0), (7.0, 72.0), (15.0, 50.0),
                             (30.0, 28.0), (60.0, 12.0)], worst=2.0),
            0.12, m.days_since_last_trade, "дней",
        ),
        ComponentScore(
            "free_float", "Free float",
            step_high_better(m.free_float_pct,
                             [(0.40, 100.0), (0.25, 85.0), (0.15, 68.0), (0.10, 50.0),
                              (0.05, 30.0)], worst=12.0),
            0.10, m.free_float_pct, "%",
        ),
        ComponentScore("quotes", "Наличие котировок", quotes, 0.08, None, None),
    ]


def liquidity_component(facts: StockFacts, weight: float) -> ComponentScore:
    children = _liquidity_children(facts)
    score = blend([(c.score, c.weight) for c in children])
    return ComponentScore(
        code="liquidity",
        label="Ликвидность",
        score=score,
        weight=weight,
        raw_value=facts.market.avg_daily_turnover,
        unit=facts.currency,
        reason="Насколько реально купить и продать бумагу по разумной цене.",
        source=facts.market.provenance.source,
        as_of=facts.market.provenance.as_of,
        children=children,
    )


def liquidity_score(facts: StockFacts) -> float | None:
    return liquidity_component(facts, 0.0).score


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------


def _business_quality(facts: StockFacts, weight: float) -> ComponentScore:
    f = facts.financials
    roe = _roe_raw(f.roe)
    reason = None
    # A high ROE bought with leverage is not quality. Scale it back as the
    # balance sheet does more of the work.
    if roe is not None and f.debt_to_equity is not None and f.debt_to_equity > 1.5:
        factor = (ramp(f.debt_to_equity, [(1.5, 100.0), (2.5, 85.0), (4.0, 65.0), (6.0, 50.0)]) or 100.0) / 100.0
        if factor < 1.0:
            reason = (
                f"ROE {f.roe * 100:.0f}% во многом обеспечен долгом "
                f"(Долг/Капитал {f.debt_to_equity:.1f}x) — вклад снижен."
            )
            roe = roe * factor

    children = [
        ComponentScore("roic", "ROIC", _roic(f.roic), 0.22, f.roic, "%"),
        ComponentScore("roe", "ROE с поправкой на долг", roe, 0.20, f.roe, "%", reason=reason),
        ComponentScore("roa", "ROA", _roa(f.roa), 0.12, f.roa, "%"),
        ComponentScore("margins", "Маржинальность",
                       blend([(_margin(f.ebitda_margin), 0.5), (_margin(f.net_margin), 0.5)]),
                       0.16, f.net_margin, "%"),
        ComponentScore("fcf_margin", "Маржа свободного потока", _fcf_margin(f.fcf_margin),
                       0.12, f.fcf_margin, "%"),
        ComponentScore("cash_conversion", "Конвертация прибыли в деньги",
                       _cash_conversion(f.cash_conversion), 0.10, f.cash_conversion, "x"),
        ComponentScore("earnings_stability", "Стабильность прибыли",
                       _fraction(f.earnings_stability), 0.08, f.earnings_stability, None),
    ]
    return ComponentScore(
        code="business_quality",
        label="Качество бизнеса",
        score=blend([(c.score, c.weight) for c in children]),
        weight=weight,
        raw_value=f.roic,
        unit="ROIC",
        reason=reason or "Отдача на капитал, маржинальность и способность превращать прибыль в деньги.",
        source=f.provenance.source,
        as_of=f.provenance.as_of,
        children=children,
    )


def _growth(facts: StockFacts, weight: float) -> ComponentScore:
    f = facts.financials
    children = [
        ComponentScore("revenue_cagr", "Рост выручки (3г)", _cagr(f.revenue_cagr_3y),
                       0.22, f.revenue_cagr_3y, "%"),
        ComponentScore("ebitda_cagr", "Рост EBITDA (3г)", _cagr(f.ebitda_cagr_3y), 0.18,
                       f.ebitda_cagr_3y, "%"),
        ComponentScore("net_income_cagr", "Рост прибыли (3г)", _cagr(f.net_income_cagr_3y), 0.20,
                       f.net_income_cagr_3y, "%"),
        ComponentScore("eps_cagr", "Рост прибыли на акцию (3г)", _cagr(f.eps_cagr_3y), 0.15,
                       f.eps_cagr_3y, "%"),
        ComponentScore("fcf_cagr", "Рост свободного потока (3г)", _cagr(f.fcf_cagr_3y), 0.12,
                       f.fcf_cagr_3y, "%"),
        ComponentScore("consistency", "Стабильность роста", _fraction(f.growth_consistency), 0.08,
                       f.growth_consistency, None),
        ComponentScore("dilution", "Размытие доли", _dilution(f.share_count_growth), 0.05,
                       f.share_count_growth, "%"),
    ]
    return ComponentScore(
        code="growth",
        label="Рост",
        score=blend([(c.score, c.weight) for c in children]),
        weight=weight,
        raw_value=f.revenue_cagr_3y,
        unit="%",
        reason="Рост считается по выручке, EBITDA, прибыли, EPS и денежному потоку; "
               "выпуск новых акций уменьшает результат.",
        source=f.provenance.source,
        as_of=f.provenance.as_of,
        children=children,
    )


def value_trap_signals(facts: StockFacts) -> list[str]:
    """The four deterioration signals from the spec, evaluated independently."""
    f = facts.financials
    signals: list[str] = []
    if f.earnings_growth is not None and f.earnings_growth < 0:
        signals.append("прибыль падает")
    if (f.fcf_growth is not None and f.fcf_growth < 0) or (
        f.free_cash_flow is not None and f.free_cash_flow < 0
    ):
        signals.append("денежный поток ухудшается")
    if f.revenue_growth is not None and f.revenue_growth < 0:
        signals.append("выручка падает")
    if f.debt_change_1y is not None and f.debt_change_1y > 0.05:
        signals.append("долг растет")
    return signals


#: How much of the valuation score survives N deterioration signals.
_VALUE_TRAP_FACTOR = {0: 1.0, 1: 1.0, 2: 0.80, 3: 0.60, 4: 0.45}


def _valuation(facts: StockFacts, weight: float) -> ComponentScore:
    pe = step_low_better(
        facts.pe, [(6.0, 100.0), (9.0, 88.0), (12.0, 76.0), (16.0, 60.0), (22.0, 40.0), (30.0, 20.0)],
        worst=6.0,
    )
    ev = step_low_better(
        facts.ev_ebitda, [(4.0, 100.0), (6.0, 88.0), (8.0, 74.0), (11.0, 58.0), (15.0, 38.0), (20.0, 18.0)],
        worst=5.0,
    )
    pb = step_low_better(
        facts.pb, [(0.8, 100.0), (1.2, 88.0), (1.8, 72.0), (2.5, 56.0), (4.0, 34.0), (6.0, 15.0)],
        worst=5.0,
    )
    fcf_yield = step_high_better(
        facts.fcf_yield, [(0.12, 100.0), (0.09, 88.0), (0.06, 72.0), (0.04, 56.0), (0.02, 38.0), (0.0, 22.0)],
        worst=5.0,
    )
    dividend = step_high_better(
        facts.dividend_yield, [(0.10, 100.0), (0.07, 86.0), (0.05, 72.0), (0.03, 55.0), (0.01, 35.0)],
        worst=20.0,
    )
    vs_history = None
    if facts.pe is not None and facts.pe_history_median:
        vs_history = step_low_better(
            facts.pe / facts.pe_history_median,
            [(0.7, 100.0), (0.85, 85.0), (1.0, 65.0), (1.2, 45.0), (1.5, 25.0)],
            worst=10.0,
        )
    vs_peers = None
    if facts.pe is not None and facts.peers.peer_median_pe:
        vs_peers = step_low_better(
            facts.pe / facts.peers.peer_median_pe,
            [(0.7, 100.0), (0.85, 85.0), (1.0, 65.0), (1.25, 45.0), (1.6, 25.0)],
            worst=10.0,
        )
    elif facts.pb is not None and facts.peers.peer_median_pb:
        vs_peers = step_low_better(
            facts.pb / facts.peers.peer_median_pb,
            [(0.7, 100.0), (0.85, 85.0), (1.0, 65.0), (1.25, 45.0), (1.6, 25.0)],
            worst=10.0,
        )

    children = [
        ComponentScore("pe", "P/E", pe, 0.25, facts.pe, "x"),
        ComponentScore("ev_ebitda", "EV/EBITDA", ev, 0.18, facts.ev_ebitda, "x"),
        ComponentScore("pb", "P/B", pb, 0.12, facts.pb, "x"),
        ComponentScore("fcf_yield", "Доходность свободного потока", fcf_yield, 0.20,
                       facts.fcf_yield, "%"),
        ComponentScore("dividend_yield", "Дивидендная доходность", dividend, 0.10,
                       facts.dividend_yield, "%"),
        ComponentScore("vs_history", "Против своей истории", vs_history, 0.05,
                       facts.pe_history_median, "x"),
        ComponentScore("vs_peers", "Против аналогов", vs_peers, 0.10,
                       facts.peers.peer_median_pe, "x"),
    ]
    score = blend([(c.score, c.weight) for c in children])

    signals = value_trap_signals(facts)
    factor = _VALUE_TRAP_FACTOR.get(len(signals), 0.45)
    reason = "Дешевизна засчитывается только при здоровом бизнесе."
    if score is not None and factor < 1.0:
        score = score * factor
        reason = (
            f"Штраф за ловушку стоимости (−{(1 - factor) * 100:.0f}%): "
            + ", ".join(signals)
            + "."
        )
    return ComponentScore(
        code="valuation",
        label="Оценка",
        score=score,
        weight=weight,
        raw_value=facts.pe,
        unit="P/E",
        reason=reason,
        source=facts.market.provenance.source,
        as_of=facts.market.provenance.as_of,
        children=children,
    )


def _financial_strength(facts: StockFacts, weight: float) -> ComponentScore:
    f = facts.financials
    leverage_raw = f.net_debt_to_ebitda if f.net_debt_to_ebitda is not None else f.debt_to_ebitda
    fcf_to_debt = None
    if f.free_cash_flow is not None and f.total_debt:
        fcf_to_debt = step_high_better(
            f.free_cash_flow / f.total_debt,
            [(0.25, 100.0), (0.15, 88.0), (0.10, 75.0), (0.05, 60.0), (0.0, 40.0)],
            worst=10.0,
        )
    elif f.free_cash_flow is not None:
        fcf_to_debt = 75.0 if f.free_cash_flow > 0 else 25.0

    maturity = None
    if f.debt_maturing_12m is not None:
        available = (f.cash or 0.0) + max(f.operating_cash_flow or 0.0, 0.0)
        ratio = None if f.debt_maturing_12m == 0 else available / f.debt_maturing_12m
        maturity = step_high_better(
            ratio if ratio is not None else 5.0,
            [(2.0, 100.0), (1.5, 88.0), (1.0, 72.0), (0.7, 50.0), (0.4, 28.0)],
            worst=8.0,
        )

    children = [
        ComponentScore("leverage", "Чистый долг / EBITDA", leverage_score(leverage_raw, f.ebitda),
                       0.30, leverage_raw, "x"),
        ComponentScore("interest_coverage", "Покрытие процентов", coverage_score(f.interest_coverage),
                       0.25, f.interest_coverage, "x"),
        ComponentScore("debt_to_equity", "Долг / Капитал", debt_to_equity_score(f.debt_to_equity),
                       0.15, f.debt_to_equity, "x"),
        ComponentScore("cash_cover", "Деньги / короткий долг",
                       cash_cover_score(f.cash_to_short_term_debt), 0.12,
                       f.cash_to_short_term_debt, "x"),
        ComponentScore("fcf_to_debt", "Свободный поток к долгу", fcf_to_debt, 0.12,
                       f.free_cash_flow, facts.currency),
        ComponentScore("debt_maturity", "Погашения ближайшего года", maturity, 0.06,
                       f.debt_maturing_12m, facts.currency),
    ]
    return ComponentScore(
        code="financial_strength",
        label="Финансовая устойчивость",
        score=blend([(c.score, c.weight) for c in children]),
        weight=weight,
        raw_value=leverage_raw,
        unit="x",
        reason="Долговая нагрузка, покрытие процентов, запас денег и график погашений.",
        source=f.provenance.source,
        as_of=f.provenance.as_of,
        children=children,
    )


def _shareholder_return(facts: StockFacts, weight: float) -> ComponentScore:
    f = facts.financials
    dividend = step_high_better(
        facts.dividend_yield, [(0.10, 100.0), (0.07, 88.0), (0.05, 74.0), (0.03, 56.0), (0.01, 34.0)],
        worst=15.0,
    )
    # Both extremes are a warning: nothing paid, or more paid than earned.
    payout = ramp(
        facts.payout_ratio,
        [(0.0, 30.0), (0.15, 65.0), (0.30, 90.0), (0.55, 100.0), (0.75, 80.0),
         (0.95, 50.0), (1.2, 15.0), (1.6, 0.0)],
    )
    fcf_payout = step_low_better(
        facts.fcf_payout_ratio,
        [(0.4, 100.0), (0.6, 88.0), (0.8, 70.0), (1.0, 48.0), (1.3, 22.0)],
        worst=5.0,
    )
    buyback = step_high_better(
        facts.buyback_yield, [(0.05, 100.0), (0.03, 85.0), (0.01, 70.0), (0.0, 55.0)],
        worst=45.0,
    )
    children = [
        ComponentScore("dividend_yield", "Дивидендная доходность", dividend, 0.30,
                       facts.dividend_yield, "%"),
        ComponentScore("payout", "Доля прибыли на дивиденды", payout, 0.25, facts.payout_ratio, "x"),
        ComponentScore("fcf_payout", "Дивиденды из свободного потока", fcf_payout, 0.20,
                       facts.fcf_payout_ratio, "x"),
        ComponentScore("buyback", "Выкуп акций", buyback, 0.10, facts.buyback_yield, "%"),
        ComponentScore("dilution", "Отсутствие размытия", _dilution(f.share_count_growth), 0.15,
                       f.share_count_growth, "%"),
    ]
    return ComponentScore(
        code="shareholder_return",
        label="Возврат акционерам",
        score=blend([(c.score, c.weight) for c in children]),
        weight=weight,
        raw_value=facts.dividend_yield,
        unit="%",
        reason="Дивиденды, их покрытие прибылью и денежным потоком, выкупы за вычетом размытия.",
        children=children,
    )


def _risk_stability(facts: StockFacts, weight: float) -> ComponentScore:
    m = facts.market
    children = [
        ComponentScore("volatility", "Волатильность",
                       step_low_better(m.price_volatility_90d,
                                       [(0.15, 100.0), (0.25, 85.0), (0.35, 68.0), (0.50, 45.0),
                                        (0.70, 22.0)], worst=5.0),
                       0.40, m.price_volatility_90d, "%"),
        ComponentScore("drawdown", "Максимальная просадка",
                       step_low_better(m.max_drawdown_1y,
                                       [(0.10, 100.0), (0.20, 85.0), (0.30, 68.0), (0.45, 45.0),
                                        (0.60, 22.0)], worst=5.0),
                       0.30, m.max_drawdown_1y, "%"),
        ComponentScore("earnings_stability", "Стабильность прибыли",
                       _fraction(facts.financials.earnings_stability), 0.30,
                       facts.financials.earnings_stability, None),
    ]
    return ComponentScore(
        code="risk_stability",
        label="Риск и стабильность",
        score=blend([(c.score, c.weight) for c in children]),
        weight=weight,
        raw_value=m.price_volatility_90d,
        unit="%",
        children=children,
    )


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


class StockScoringEngine:
    """``stock_score_v1``. Banks are delegated to :class:`BankScoringEngine`."""

    version = STOCK_MODEL

    def __init__(self) -> None:
        self.flags = RedFlagEngine()
        self.data_quality = DataQualityEngine()
        self.confidence = ScoreConfidenceEngine()

    def _checks(self, facts: StockFacts) -> list[FieldCheck]:
        f = facts.financials
        leverage = f.net_debt_to_ebitda if f.net_debt_to_ebitda is not None else f.debt_to_ebitda
        return [
            FieldCheck("valuation", "оценка (P/E или EV/EBITDA)",
                       facts.pe is not None or facts.ev_ebitda is not None, critical=True),
            FieldCheck("leverage", "долговая нагрузка", leverage is not None, critical=True),
            FieldCheck("coverage", "покрытие процентов", f.interest_coverage is not None, critical=True),
            FieldCheck("profitability", "рентабельность", f.roe is not None or f.roic is not None,
                       critical=True),
            FieldCheck("cash_flow", "денежный поток",
                       f.free_cash_flow is not None or f.operating_cash_flow is not None, critical=True),
            FieldCheck("growth", "динамика бизнеса",
                       f.revenue_cagr_3y is not None or f.revenue_growth is not None),
            FieldCheck("dividends", "дивиденды", facts.dividend_yield is not None),
            FieldCheck("quotes", "котировки",
                       facts.market.bid is not None and facts.market.ask is not None),
            FieldCheck("turnover", "объем торгов", facts.market.avg_daily_turnover is not None),
            FieldCheck("equity", "капитал", f.equity is not None),
        ]

    def score(self, facts: StockFacts, *, as_of: datetime | None = None) -> StrictScore:
        if facts.is_bank:
            from app.scoring.strict.banks import BankScoringEngine

            return BankScoringEngine().score(facts, as_of=as_of)

        view = as_of_view(facts, as_of)
        f: StockFacts = view.facts
        derive_financials(f.financials)
        now = datetime.now(timezone.utc)
        moment = view.as_of or now

        dq_input = self.data_quality.evaluate(f, self._checks(f), moment=moment)
        liquidity = liquidity_component(f, STOCK_WEIGHTS["liquidity"])

        components = [
            _business_quality(f, STOCK_WEIGHTS["business_quality"]),
            _growth(f, STOCK_WEIGHTS["growth"]),
            _valuation(f, STOCK_WEIGHTS["valuation"]),
            _financial_strength(f, STOCK_WEIGHTS["financial_strength"]),
            _shareholder_return(f, STOCK_WEIGHTS["shareholder_return"]),
            liquidity,
            _risk_stability(f, STOCK_WEIGHTS["risk_stability"]),
            ComponentScore("data_quality", "Качество данных", dq_input.value,
                           STOCK_WEIGHTS["data_quality"],
                           reason="Полнота, свежесть и официальность источников."),
        ]

        flags = self.flags.for_stock(
            f,
            missing_critical=dq_input.missing_critical,
            value_trap_signals=value_trap_signals(f),
        )
        confidence = self.confidence.evaluate(f, data_quality=dq_input, liquidity_score=liquidity.score)
        state = stock_cap_state(f, component_map(components), flags)

        return finalise(
            kind="stock",
            ticker=f.ticker,
            model=self.version,
            components=components,
            flags=flags,
            cap_rules=STOCK_CAPS,
            cap_state=state,
            data_quality=dq_input,
            confidence=confidence,
            as_of=view.as_of,
            excluded_facts=view.excluded,
            notes=[],
            now=now,
        )
