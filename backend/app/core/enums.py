"""Shared enumerations.

Stored as plain strings in the database so that adding a value never requires a
migration of a PostgreSQL ENUM type.
"""

from __future__ import annotations

from enum import StrEnum


class DataMode(StrEnum):
    """Freshness/authenticity marker attached to every market data point."""

    LIVE = "live"
    DELAYED = "delayed"
    END_OF_DAY = "end_of_day"
    CACHED = "cached"
    MOCK = "mock"  # never allowed to reach a production response


class SourceKind(StrEnum):
    OFFICIAL_API = "official_api"
    WEBSITE = "website"
    MANUAL = "manual"
    CALCULATED = "calculated"
    MOCK = "mock"


class CouponType(StrEnum):
    FIXED = "fixed"
    FLOATING = "floating"
    ZERO = "zero"
    INDEXED = "indexed"
    STEP = "step"


class BondType(StrEnum):
    GOVERNMENT = "government"
    MUNICIPAL = "municipal"
    CORPORATE = "corporate"
    BANK = "bank"
    QUASI_SOVEREIGN = "quasi_sovereign"
    INTERNATIONAL = "international"


class IssuerSector(StrEnum):
    SOVEREIGN = "sovereign"
    BANK = "bank"
    FINANCIAL = "financial"
    CORPORATE = "corporate"
    QUASI_SOVEREIGN = "quasi_sovereign"


class UiMode(StrEnum):
    SIMPLE = "simple"
    PRO = "pro"


class RiskProfile(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class InflationSource(StrEnum):
    AUTOMATIC = "automatic"
    OFFICIAL = "official"
    FORECAST = "forecast"
    MANUAL = "manual"


class ScoreKind(StrEnum):
    INVESTMENT = "investment"
    CREDIT = "credit"
    LIQUIDITY = "liquidity"
    GROWTH = "growth"
    INCOME = "income"
    REAL_RETURN = "real_return"
    RISK_REWARD = "risk_reward"
    STABILITY = "stability"
    EXIT = "exit"
    RELATIVE_VALUE = "relative_value"
    DATA_QUALITY = "data_quality"
    ANALYSIS_CONFIDENCE = "analysis_confidence"
    HOLD = "hold"
    TRADE = "trade"
    PERSONAL = "personal"


class AlertKind(StrEnum):
    PRICE_BELOW = "price_below"
    PRICE_ABOVE = "price_above"
    YTM_ABOVE = "ytm_above"
    YTM_BELOW = "ytm_below"
    SCORE_ABOVE = "score_above"
    COUPON_DATE = "coupon_date"
    MATURITY_DATE = "maturity_date"
    PE_BELOW = "pe_below"
    DIVIDEND_ANNOUNCED = "dividend_announced"
    FINANCIAL_REPORT = "financial_report"
    PROFIT_CHANGE = "profit_change"
    SCORE_CHANGE = "score_change"
    COMPANY_NEWS = "company_news"


class DayCount(StrEnum):
    ACT_365F = "ACT/365F"
    ACT_360 = "ACT/360"
    THIRTY_360 = "30/360"
    ACT_ACT = "ACT/ACT"
