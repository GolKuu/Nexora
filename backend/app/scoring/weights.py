"""Scoring weights.

Weights live on the backend only. The frontend renders whatever weights the API
reports and never carries its own copy - otherwise the two drift and the score
shown stops matching the score explained.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCORING_MODEL_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class WeightSet:
    version: str
    profile: str
    investment: dict[str, float]
    credit_corporate: dict[str, float]
    credit_bank: dict[str, float]
    liquidity: dict[str, float]
    income: dict[str, float]
    growth: dict[str, float]
    stability: dict[str, float]
    hold: dict[str, float]
    trade: dict[str, float]
    data_quality: dict[str, float] = field(default_factory=dict)


_INVESTMENT_BASE = {
    "credit_quality": 0.26,
    "risk_reward": 0.18,
    "real_return": 0.18,
    "relative_value": 0.14,
    "liquidity": 0.12,
    "growth": 0.07,
    "data_quality": 0.05,
}

_CREDIT_CORPORATE = {
    "leverage": 0.24,          # Net Debt / EBITDA
    "interest_coverage": 0.18,
    "liquidity_ratios": 0.12,  # current / quick
    "cash_generation": 0.14,   # OCF, FCF
    "profitability": 0.12,     # ROA, ROE, EBITDA margin
    "capital_structure": 0.10,  # Debt / Equity
    "dynamics": 0.10,          # revenue and profit trend
}

_CREDIT_BANK = {
    "capital_adequacy": 0.26,
    "asset_quality": 0.22,     # NPL ratio, provision coverage
    "liquidity": 0.18,         # liquid assets, loan / deposit
    "profitability": 0.16,     # ROA, ROE, NIM
    "efficiency": 0.08,        # cost / income
    "funding_structure": 0.10,
}

_LIQUIDITY = {
    "turnover": 0.35,
    "trading_frequency": 0.30,
    "bid_ask": 0.25,
    "issue_size": 0.10,
}

_INCOME = {
    "coupon_level": 0.40,
    "coupon_certainty": 0.30,   # fixed beats floating for income planning
    "payment_frequency": 0.15,
    "issuer_capacity": 0.15,
}

_GROWTH = {
    "discount_to_par": 0.40,
    "duration_upside": 0.30,
    "spread_compression": 0.30,
}

_STABILITY = {
    "price_volatility": 0.40,
    "duration_risk": 0.35,
    "credit_stability": 0.25,
}

_HOLD = {
    "real_return": 0.35,
    "credit_quality": 0.35,
    "income": 0.20,
    "coupon_certainty": 0.10,
}

_TRADE = {
    "liquidity": 0.40,
    "growth": 0.35,
    "relative_value": 0.25,
}

_DATA_QUALITY = {
    "market_data": 0.35,
    "reference_data": 0.25,
    "financials": 0.25,
    "freshness": 0.15,
}


def _reweight(base: dict[str, float], overrides: dict[str, float]) -> dict[str, float]:
    merged = {**base, **overrides}
    total = sum(merged.values())
    return {k: v / total for k, v in merged.items()}


#: Risk profile only re-weights the Investment Score; sub-scores stay objective.
_PROFILE_OVERRIDES: dict[str, dict[str, float]] = {
    "conservative": {"credit_quality": 0.34, "liquidity": 0.16, "growth": 0.03},
    "balanced": {},
    "aggressive": {"credit_quality": 0.18, "growth": 0.14, "risk_reward": 0.22},
}


def get_weights(profile: str = "balanced") -> WeightSet:
    overrides = _PROFILE_OVERRIDES.get(profile, {})
    return WeightSet(
        version=SCORING_MODEL_VERSION,
        profile=profile if profile in _PROFILE_OVERRIDES else "balanced",
        investment=_reweight(_INVESTMENT_BASE, overrides),
        credit_corporate=dict(_CREDIT_CORPORATE),
        credit_bank=dict(_CREDIT_BANK),
        liquidity=dict(_LIQUIDITY),
        income=dict(_INCOME),
        growth=dict(_GROWTH),
        stability=dict(_STABILITY),
        hold=dict(_HOLD),
        trade=dict(_TRADE),
        data_quality=dict(_DATA_QUALITY),
    )


#: Human labels, kept next to the weights so the API can explain itself.
COMPONENT_LABELS: dict[str, str] = {
    "credit_quality": "Надежность эмитента",
    "risk_reward": "Доходность к риску",
    "real_return": "Доход после инфляции",
    "relative_value": "Выгоднее похожих бумаг",
    "liquidity": "Легко купить и продать",
    "growth": "Потенциал роста цены",
    "data_quality": "Полнота данных",
    "leverage": "Долговая нагрузка",
    "interest_coverage": "Покрытие процентов",
    "liquidity_ratios": "Текущая ликвидность",
    "cash_generation": "Денежный поток",
    "profitability": "Прибыльность",
    "capital_structure": "Структура капитала",
    "dynamics": "Динамика бизнеса",
    "capital_adequacy": "Достаточность капитала",
    "asset_quality": "Качество кредитного портфеля",
    "efficiency": "Операционная эффективность",
    "funding_structure": "Структура фондирования",
    "turnover": "Объем торгов",
    "trading_frequency": "Частота сделок",
    "bid_ask": "Спред покупки и продажи",
    "issue_size": "Размер выпуска",
    "coupon_level": "Размер купона",
    "coupon_certainty": "Предсказуемость выплат",
    "payment_frequency": "Частота выплат",
    "issuer_capacity": "Способность платить",
    "discount_to_par": "Скидка к номиналу",
    "duration_upside": "Чувствительность к ставкам",
    "spread_compression": "Запас по спреду",
    "price_volatility": "Колебания цены",
    "duration_risk": "Процентный риск",
    "credit_stability": "Стабильность рейтинга",
    "market_data": "Рыночные данные",
    "reference_data": "Параметры выпуска",
    "financials": "Отчетность эмитента",
    "freshness": "Свежесть данных",
    "income": "Качество выплат",
}
