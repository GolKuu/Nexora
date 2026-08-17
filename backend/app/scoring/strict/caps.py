"""Hard caps.

A cap is an absolute ceiling on the final score. Unlike weights, caps are not
negotiable and not averaged away: if credit quality is below 30, the bond cannot
score above 45 no matter how attractive its yield, liquidity or structure look.

This is the mechanism that enforces the central rule of the system - one
attractive metric must never be enough to produce a high final score.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.scoring.strict.facts import BondFacts, StockFacts
from app.scoring.strict.results import AppliedCap, RedFlag
from app.scoring.strict.versions import CAP_VERSION


@dataclass(frozen=True, slots=True)
class CapRule:
    code: str
    ceiling: float
    reason: str
    predicate: Callable[[dict], bool]


def _score(state: dict, code: str) -> float:
    """Component score lookup; a missing component never triggers a cap."""
    value = state.get(code)
    return 101.0 if value is None else float(value)


def _flagged(state: dict, *codes: str) -> bool:
    flags: set[str] = state.get("flags", set())
    return any(code in flags for code in codes)


# ---------------------------------------------------------------------------
# bond caps (spec section 3)
# ---------------------------------------------------------------------------

BOND_CAPS: tuple[CapRule, ...] = (
    CapRule(
        "DEFAULT_OR_MISSED_PAYMENT", 10.0,
        "Дефолт или пропущенная выплата: бумага не является инвестицией.",
        lambda s: _flagged(s, "DEFAULT", "MISSED_PAYMENT"),
    ),
    CapRule(
        "RESTRUCTURING", 20.0,
        "Реструктуризация долга ограничивает оценку.",
        lambda s: _flagged(s, "RESTRUCTURING"),
    ),
    CapRule(
        "CREDIT_BELOW_15", 25.0,
        "Кредитное качество ниже 15: риск потери капитала преобладает.",
        lambda s: _score(s, "credit_quality") < 15.0,
    ),
    CapRule(
        "SOLVENCY_COLLAPSE", 25.0,
        "Отрицательный капитал, отрицательный FCF и растущий долг одновременно.",
        lambda s: bool(s.get("solvency_collapse")),
    ),
    CapRule(
        "COVERAGE_AND_LEVERAGE", 30.0,
        "Покрытие процентов ниже 1x при долге выше 5x EBITDA.",
        lambda s: bool(s.get("coverage_and_leverage")),
    ),
    CapRule(
        "CREDIT_BELOW_30", 45.0,
        "Кредитное качество ниже 30 ограничивает итоговую оценку.",
        lambda s: _score(s, "credit_quality") < 30.0,
    ),
    CapRule(
        "DATA_QUALITY_BELOW_40", 55.0,
        "Данных слишком мало, чтобы обосновать высокую оценку.",
        lambda s: _score(s, "data_quality") < 40.0,
    ),
    CapRule(
        "LIQUIDITY_BELOW_15", 60.0,
        "Практически нет ликвидности: из позиции трудно выйти.",
        lambda s: _score(s, "liquidity") < 15.0,
    ),
)


# ---------------------------------------------------------------------------
# stock caps (spec section 6)
# ---------------------------------------------------------------------------

STOCK_CAPS: tuple[CapRule, ...] = (
    CapRule(
        "GOING_CONCERN", 20.0,
        "Сомнения в непрерывности деятельности.",
        lambda s: _flagged(s, "GOING_CONCERN"),
    ),
    CapRule(
        "AUDITOR_ADVERSE", 25.0,
        "Отрицательное заключение аудитора или отказ от мнения.",
        lambda s: _flagged(s, "AUDITOR_ADVERSE"),
    ),
    CapRule(
        "AUDITOR_QUALIFIED", 55.0,
        "Аудиторское заключение с оговоркой.",
        lambda s: _flagged(s, "AUDITOR_QUALIFIED"),
    ),
    CapRule(
        "NEGATIVE_EQUITY", 45.0,
        "Отрицательный собственный капитал.",
        lambda s: bool(s.get("negative_equity")) and not bool(s.get("is_bank")),
    ),
    CapRule(
        "CASH_BURN_AND_DEBT", 50.0,
        "Три года отрицательного FCF при растущем долге.",
        lambda s: bool(s.get("cash_burn_and_debt")),
    ),
    CapRule(
        "DATA_QUALITY_BELOW_40", 55.0,
        "Данных слишком мало, чтобы обосновать высокую оценку.",
        lambda s: _score(s, "data_quality") < 40.0,
    ),
    CapRule(
        "FINANCIAL_STRENGTH_BELOW_25", 60.0,
        "Финансовая устойчивость ниже 25.",
        lambda s: _score(s, "financial_strength") < 25.0,
    ),
    CapRule(
        "SEVERE_DILUTION", 60.0,
        "Сильное размытие доли акционеров.",
        lambda s: _flagged(s, "SEVERE_DILUTION"),
    ),
    CapRule(
        "LIQUIDITY_BELOW_15", 65.0,
        "Практически нет ликвидности: из позиции трудно выйти.",
        lambda s: _score(s, "liquidity") < 15.0,
    ),
)


# ---------------------------------------------------------------------------
# bank caps
# ---------------------------------------------------------------------------

BANK_CAPS: tuple[CapRule, ...] = (
    CapRule(
        "GOING_CONCERN", 20.0,
        "Сомнения в непрерывности деятельности банка.",
        lambda s: _flagged(s, "GOING_CONCERN"),
    ),
    CapRule(
        "NEGATIVE_EQUITY", 20.0,
        "Отрицательный капитал банка.",
        lambda s: bool(s.get("negative_equity")),
    ),
    CapRule(
        "CAPITAL_BELOW_MINIMUM", 30.0,
        "Достаточность капитала ниже регуляторного минимума.",
        lambda s: bool(s.get("capital_below_minimum")),
    ),
    CapRule(
        "CAPITAL_STRENGTH_BELOW_25", 50.0,
        "Капитальная позиция ниже 25.",
        lambda s: _score(s, "capital_strength") < 25.0,
    ),
    CapRule(
        "ASSET_QUALITY_BELOW_25", 55.0,
        "Качество активов ниже 25.",
        lambda s: _score(s, "asset_quality") < 25.0,
    ),
    CapRule(
        "DATA_QUALITY_BELOW_40", 55.0,
        "Данных слишком мало, чтобы обосновать высокую оценку.",
        lambda s: _score(s, "data_quality") < 40.0,
    ),
    CapRule(
        "LIQUIDITY_BELOW_15", 65.0,
        "Практически нет ликвидности: из позиции трудно выйти.",
        lambda s: _score(s, "liquidity") < 15.0,
    ),
)


class ScoreCapEngine:
    """Evaluates cap rules and applies the strictest binding ceiling."""

    version = CAP_VERSION

    def evaluate(self, rules: tuple[CapRule, ...], state: dict) -> list[AppliedCap]:
        triggered = [
            AppliedCap(code=rule.code, ceiling=rule.ceiling, reason=rule.reason)
            for rule in rules
            if rule.predicate(state)
        ]
        return sorted(triggered, key=lambda c: (c.ceiling, c.code))

    def apply(self, score: float, caps: list[AppliedCap]) -> tuple[float, list[AppliedCap]]:
        """Return the capped score plus the caps with ``binding`` resolved.

        A cap is *binding* when it is the strictest ceiling and it actually
        lowers the score. Caps that trigger but sit above the score are still
        reported - the user should know how little headroom is left.
        """
        if not caps:
            return score, []
        strictest = min(c.ceiling for c in caps)
        final = min(score, strictest)
        resolved = [
            AppliedCap(
                code=c.code,
                ceiling=c.ceiling,
                reason=c.reason,
                binding=(c.ceiling == strictest and strictest < score),
            )
            for c in caps
        ]
        return final, resolved


# ---------------------------------------------------------------------------
# state builders
# ---------------------------------------------------------------------------


def bond_cap_state(
    facts: BondFacts, components: dict[str, float | None], flags: list[RedFlag]
) -> dict:
    f = facts.financials
    leverage = f.net_debt_to_ebitda if f.net_debt_to_ebitda is not None else f.debt_to_ebitda
    coverage_and_leverage = (
        f.interest_coverage is not None
        and leverage is not None
        and f.interest_coverage < 1.0
        and leverage > 5.0
    )
    solvency_collapse = (
        f.equity is not None and f.equity < 0
        and f.free_cash_flow is not None and f.free_cash_flow < 0
        and f.debt_change_1y is not None and f.debt_change_1y > 0
    )
    return {
        **components,
        "flags": {flag.code for flag in flags},
        "coverage_and_leverage": coverage_and_leverage,
        "solvency_collapse": solvency_collapse,
        "is_bank": facts.is_bank_issuer,
    }


def stock_cap_state(
    facts: StockFacts, components: dict[str, float | None], flags: list[RedFlag]
) -> dict:
    f = facts.financials
    cash_burn_and_debt = (
        (f.negative_fcf_years or 0) >= 3
        and f.debt_change_1y is not None
        and f.debt_change_1y > 0
    )
    return {
        **components,
        "flags": {flag.code for flag in flags},
        "negative_equity": f.equity is not None and f.equity < 0,
        "cash_burn_and_debt": cash_burn_and_debt,
        "is_bank": facts.is_bank,
    }


def bank_cap_state(
    facts: StockFacts | BondFacts, components: dict[str, float | None], flags: list[RedFlag]
) -> dict:
    bank = facts.bank_financials
    return {
        **components,
        "flags": {flag.code for flag in flags},
        "negative_equity": bool(bank and bank.equity is not None and bank.equity < 0),
        "capital_below_minimum": bool(
            bank
            and bank.capital_adequacy_ratio is not None
            and bank.capital_adequacy_ratio < 0.08
        ),
        "is_bank": True,
    }
