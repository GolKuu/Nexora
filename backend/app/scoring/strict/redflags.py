"""Red flag detection.

Red flags are not decoration for the UI: every flag carries a penalty in score
points that is subtracted from the weighted base score before the hard caps are
applied. A flag that only lit up an icon would let a bond with a missed payment
keep an attractive number, which is exactly what this system exists to prevent.

The catastrophic flags (default, missed payment, restructuring) also trigger
hard caps in :mod:`app.scoring.strict.caps`. The penalty and the cap are
deliberately redundant - the cap decides the ceiling, the penalty makes sure the
score is already low before the ceiling is even consulted.
"""

from __future__ import annotations

from app.scoring.strict.facts import BondFacts, StockFacts
from app.scoring.strict.results import RedFlag
from app.scoring.strict.scale import rating_notches
from app.scoring.strict.versions import RED_FLAG_VERSION

#: Ceiling on the combined penalty. Beyond this the hard caps, not the running
#: subtraction, are what keeps the score honest - and stacking every penalty
#: linearly would double-count the same underlying problem.
MAX_TOTAL_PENALTY = 25.0

#: Flags whose signal is *also* a weighted component (leverage, coverage, cash
#: flow) carry a deliberately small penalty: the component already moved the
#: score, and the flag's job is to make the reason visible and to add a nudge,
#: not to punish the same fact twice. Flags for facts no component measures -
#: credit events, dead order books, source conflicts - carry the real weight.

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"


def _flag(code: str, severity: str, message: str, penalty: float, **evidence) -> RedFlag:
    return RedFlag(
        code=code,
        severity=severity,
        message=message,
        penalty=penalty,
        evidence={k: v for k, v in evidence.items() if v is not None},
    )


class RedFlagEngine:
    """Deterministic flag detection shared by bonds, stocks and banks."""

    version = RED_FLAG_VERSION

    # -- shared checks ---------------------------------------------------

    def _credit_event_flags(self, facts: BondFacts | StockFacts) -> list[RedFlag]:
        events = facts.events
        flags: list[RedFlag] = []
        if events.in_default:
            flags.append(_flag("DEFAULT", CRITICAL, "Эмитент в дефолте.", 40.0))
        if events.missed_payment:
            flags.append(
                _flag("MISSED_PAYMENT", CRITICAL, "Есть пропущенная выплата по обязательствам.", 35.0)
            )
        if events.restructuring:
            flags.append(
                _flag("RESTRUCTURING", CRITICAL, "Проводилась реструктуризация долга.", 30.0)
            )
        if events.default_history and not events.in_default:
            flags.append(
                _flag("DEFAULT_HISTORY", HIGH, "В истории эмитента был дефолт.", 10.0)
            )
        notches = rating_notches(events.rating_previous, events.rating)
        if notches is not None and notches > 0:
            flags.append(
                _flag(
                    "RATING_DOWNGRADE",
                    HIGH if notches >= 2 else MEDIUM,
                    f"Рейтинг понижен на {notches} ступен(и): {events.rating_previous} → {events.rating}.",
                    min(4.0 + 3.0 * notches, 12.0),
                    notches=notches,
                )
            )
        elif (events.rating_outlook or "").lower() in ("negative", "негативный"):
            flags.append(
                _flag("NEGATIVE_OUTLOOK", LOW, "Негативный прогноз по рейтингу.", 3.0)
            )
        if events.covenant_breach:
            flags.append(_flag("COVENANT_BREACH", HIGH, "Нарушение ковенант.", 8.0))
        if events.going_concern_doubt:
            flags.append(
                _flag(
                    "GOING_CONCERN",
                    CRITICAL,
                    "Аудитор указал на сомнения в непрерывности деятельности.",
                    35.0,
                )
            )
        opinion = (events.auditor_opinion or "").lower()
        if opinion in ("adverse", "disclaimer"):
            flags.append(
                _flag(
                    "AUDITOR_ADVERSE",
                    CRITICAL,
                    "Отрицательное аудиторское заключение или отказ от выражения мнения.",
                    30.0,
                    opinion=opinion,
                )
            )
        elif opinion == "qualified":
            flags.append(
                _flag("AUDITOR_QUALIFIED", HIGH, "Аудиторское заключение с оговоркой.", 12.0)
            )
        return flags

    def _balance_sheet_flags(self, facts: BondFacts | StockFacts) -> list[RedFlag]:
        f = facts.financials
        flags: list[RedFlag] = []
        if f.equity is not None and f.equity < 0:
            flags.append(
                _flag("NEGATIVE_EQUITY", HIGH, "Отрицательный собственный капитал.", 12.0, equity=f.equity)
            )
        if f.ebitda is not None and f.ebitda <= 0:
            flags.append(
                _flag("NEGATIVE_EBITDA", HIGH, "EBITDA отрицательна или равна нулю.", 12.0, ebitda=f.ebitda)
            )
        if f.free_cash_flow is not None and f.free_cash_flow < 0:
            years = f.negative_fcf_years or 1
            flags.append(
                _flag(
                    "NEGATIVE_FCF",
                    HIGH if years >= 3 else MEDIUM,
                    f"Отрицательный свободный денежный поток ({years} г.).",
                    2.0 + 1.0 * min(years, 3),
                    free_cash_flow=f.free_cash_flow,
                    years=years,
                )
            )
        leverage = f.net_debt_to_ebitda if f.net_debt_to_ebitda is not None else f.debt_to_ebitda
        if leverage is not None and leverage > 5.0:
            flags.append(
                _flag(
                    "HIGH_LEVERAGE",
                    HIGH,
                    f"Долговая нагрузка {leverage:.1f}x EBITDA.",
                    min(3.0 + (leverage - 5.0), 8.0),
                    leverage=leverage,
                )
            )
        if f.interest_coverage is not None and f.interest_coverage < 1.5:
            flags.append(
                _flag(
                    "WEAK_INTEREST_COVERAGE",
                    HIGH,
                    f"Прибыли хватает лишь на {f.interest_coverage:.1f}x процентных платежей.",
                    8.0 if f.interest_coverage < 1.0 else 5.0,
                    interest_coverage=f.interest_coverage,
                )
            )
        if f.debt_maturing_12m is not None:
            available = (f.cash or 0.0) + max(f.operating_cash_flow or 0.0, 0.0)
            if f.debt_maturing_12m > available:
                flags.append(
                    _flag(
                        "REFINANCING_WALL",
                        HIGH,
                        "Погашения в ближайший год превышают денежные средства и операционный поток.",
                        7.0,
                        debt_maturing_12m=f.debt_maturing_12m,
                        available=available,
                    )
                )
        if f.debt_change_1y is not None and f.debt_change_1y > 0.25:
            flags.append(
                _flag(
                    "RISING_DEBT",
                    MEDIUM,
                    f"Долг вырос на {f.debt_change_1y * 100:.0f}% за год.",
                    4.0,
                    debt_change_1y=f.debt_change_1y,
                )
            )
        return flags

    def _liquidity_flags(self, facts: BondFacts | StockFacts) -> list[RedFlag]:
        m = facts.market
        flags: list[RedFlag] = []
        if m.bid is None or m.bid <= 0:
            flags.append(_flag("NO_BID", HIGH, "Нет заявок на покупку: продать может быть некому.", 6.0))
        if m.ask is None or m.ask <= 0:
            flags.append(_flag("NO_ASK", MEDIUM, "Нет заявок на продажу.", 3.0))
        if m.days_since_last_trade is not None and m.days_since_last_trade > 10:
            flags.append(
                _flag(
                    "STALE_PRICE",
                    MEDIUM if m.days_since_last_trade <= 30 else HIGH,
                    f"Последняя сделка была {m.days_since_last_trade:.0f} дн. назад.",
                    3.0 if m.days_since_last_trade <= 30 else 6.0,
                    days_since_last_trade=m.days_since_last_trade,
                )
            )
        extreme = (
            (m.trade_count_30d is not None and m.trade_count_30d < 3)
            or (m.avg_daily_turnover is not None and m.avg_daily_turnover < 1e5)
            or (m.days_since_last_trade is not None and m.days_since_last_trade > 60)
        )
        if extreme:
            flags.append(
                _flag(
                    "EXTREME_ILLIQUIDITY",
                    HIGH,
                    "Бумага почти не торгуется: выход из позиции не гарантирован.",
                    9.0,
                    trade_count_30d=m.trade_count_30d,
                    avg_daily_turnover=m.avg_daily_turnover,
                )
            )
        spread = m.derived_spread_pct()
        if spread is not None and spread > 0.05:
            flags.append(
                _flag("WIDE_SPREAD", MEDIUM, f"Спред {spread * 100:.1f}%.", 4.0, spread_pct=spread)
            )
        return flags

    def _data_flags(
        self, facts: BondFacts | StockFacts, missing_critical: list[str]
    ) -> list[RedFlag]:
        flags: list[RedFlag] = []
        if facts.meta.source_conflicts:
            flags.append(
                _flag(
                    "SOURCE_CONFLICT",
                    MEDIUM,
                    f"Источники расходятся в данных ({facts.meta.source_conflicts}).",
                    5.0,
                    conflicts=facts.meta.source_conflicts,
                )
            )
        if (facts.meta.data_mode or "").lower() == "mock":
            flags.append(
                _flag("MOCK_DATA", HIGH, "Демонстрационные данные: оценка нерыночная.", 15.0)
            )
        if missing_critical:
            flags.append(
                _flag(
                    "MISSING_CRITICAL_DATA",
                    MEDIUM if len(missing_critical) < 3 else HIGH,
                    "Нет ключевых данных о риске: " + ", ".join(sorted(missing_critical)) + ".",
                    min(2.0 * len(missing_critical), 10.0),
                    missing=sorted(missing_critical),
                )
            )
        return flags

    # -- entry points ----------------------------------------------------

    def for_bond(
        self,
        facts: BondFacts,
        *,
        missing_critical: list[str] | None = None,
        real_return: float | None = None,
    ) -> list[RedFlag]:
        flags = [
            *self._credit_event_flags(facts),
            *self._liquidity_flags(facts),
            *self._data_flags(facts, missing_critical or []),
        ]
        if not facts.is_bank_issuer:
            flags.extend(self._balance_sheet_flags(facts))
        else:
            flags.extend(self._bank_flags(facts))
        if real_return is not None and real_return < -0.02:
            flags.append(
                _flag(
                    "NEGATIVE_REAL_RETURN",
                    MEDIUM,
                    "Доходность заметно ниже инфляции.",
                    4.0,
                    real_return=real_return,
                )
            )
        if facts.subordinated:
            flags.append(
                _flag("SUBORDINATED", MEDIUM, "Субординированный выпуск: выплаты в последнюю очередь.", 3.0)
            )
        return sort_flags(flags)

    def for_stock(
        self,
        facts: StockFacts,
        *,
        missing_critical: list[str] | None = None,
        value_trap_signals: list[str] | None = None,
    ) -> list[RedFlag]:
        flags = [
            *self._credit_event_flags(facts),
            *self._liquidity_flags(facts),
            *self._data_flags(facts, missing_critical or []),
        ]
        if facts.is_bank:
            flags.extend(self._bank_flags(facts))
        else:
            flags.extend(self._balance_sheet_flags(facts))

        dilution = facts.financials.share_count_growth
        if dilution is not None and dilution > 0.05:
            flags.append(
                _flag(
                    "SEVERE_DILUTION" if dilution > 0.15 else "DILUTION",
                    HIGH if dilution > 0.15 else MEDIUM,
                    f"Количество акций выросло на {dilution * 100:.0f}% за год.",
                    12.0 if dilution > 0.15 else 5.0,
                    share_count_growth=dilution,
                )
            )
        signals = value_trap_signals or []
        if len(signals) >= 3:
            flags.append(
                _flag(
                    "VALUE_TRAP",
                    HIGH,
                    "Дешевая оценка на фоне ухудшения бизнеса: " + ", ".join(signals) + ".",
                    10.0,
                    signals=signals,
                )
            )
        if facts.payout_ratio is not None and facts.payout_ratio > 1.0:
            flags.append(
                _flag(
                    "UNCOVERED_DIVIDEND",
                    MEDIUM,
                    "Дивиденды превышают прибыль.",
                    5.0,
                    payout_ratio=facts.payout_ratio,
                )
            )
        return sort_flags(flags)

    def _bank_flags(self, facts: BondFacts | StockFacts) -> list[RedFlag]:
        bank = facts.bank_financials
        flags: list[RedFlag] = []
        if bank is None:
            return flags
        if bank.capital_adequacy_ratio is not None and bank.capital_adequacy_ratio < 0.10:
            flags.append(
                _flag(
                    "THIN_CAPITAL",
                    CRITICAL if bank.capital_adequacy_ratio < 0.08 else HIGH,
                    f"Достаточность капитала {bank.capital_adequacy_ratio * 100:.1f}% — у регуляторного минимума.",
                    18.0 if bank.capital_adequacy_ratio < 0.08 else 9.0,
                    capital_adequacy_ratio=bank.capital_adequacy_ratio,
                )
            )
        if bank.npl_ratio is not None and bank.npl_ratio > 0.10:
            flags.append(
                _flag(
                    "HIGH_NPL",
                    HIGH if bank.npl_ratio <= 0.20 else CRITICAL,
                    f"Доля проблемных кредитов {bank.npl_ratio * 100:.1f}%.",
                    8.0 if bank.npl_ratio <= 0.20 else 14.0,
                    npl_ratio=bank.npl_ratio,
                )
            )
        if bank.npl_coverage is not None and bank.npl_coverage < 0.6:
            flags.append(
                _flag(
                    "LOW_NPL_COVERAGE",
                    HIGH,
                    "Резервы покрывают менее 60% проблемных кредитов.",
                    7.0,
                    npl_coverage=bank.npl_coverage,
                )
            )
        if bank.loan_to_deposit is not None and bank.loan_to_deposit > 1.3:
            flags.append(
                _flag(
                    "FUNDING_GAP",
                    MEDIUM,
                    "Кредиты существенно превышают депозиты: зависимость от оптового фондирования.",
                    5.0,
                    loan_to_deposit=bank.loan_to_deposit,
                )
            )
        if bank.roe is not None and bank.roe < 0:
            flags.append(_flag("BANK_LOSS", HIGH, "Банк убыточен.", 10.0, roe=bank.roe))
        if bank.equity is not None and bank.equity < 0:
            flags.append(_flag("NEGATIVE_EQUITY", CRITICAL, "Отрицательный капитал банка.", 20.0))
        return flags


_SEVERITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}


def sort_flags(flags: list[RedFlag]) -> list[RedFlag]:
    """Stable, deterministic ordering: severity first, then penalty, then code."""
    return sorted(
        flags,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), -f.penalty, f.code),
    )


def total_penalty(flags: list[RedFlag]) -> float:
    return min(sum(f.penalty for f in flags), MAX_TOTAL_PENALTY)
