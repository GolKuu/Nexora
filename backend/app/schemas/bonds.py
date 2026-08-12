from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Freshness


class BondListItem(BaseModel):
    id: int
    ticker: str
    isin: str | None = None
    name: str
    issuer_name: str | None = None
    currency: str
    bond_type: str | None = None
    maturity_date: str | None = None
    years_to_maturity: float | None = None
    coupon_rate_pct: float | None = None
    yield_pct: float | None = None
    real_yield_pct: float | None = None
    clean_price: float | None = None
    investment_score: float | None = None
    data_mode: str | None = None


class BondListResponse(BaseModel):
    items: list[BondListItem]
    total: int
    limit: int
    offset: int
    data_mode: str | None = None
    warning: str | None = None


class SimpleView(BaseModel):
    yield_pct: float | None = None
    real_yield_pct: float | None = None
    inflation_pct: float | None = None
    years_to_maturity: float | None = None
    maturity_date: str | None = None
    reliability: dict[str, Any]
    liquidity: dict[str, Any]
    growth_potential: dict[str, Any]
    overall: dict[str, Any]


class BondCardResponse(BaseModel):
    bond: dict[str, Any]
    simple: SimpleView
    pro: dict[str, Any]
    scores: dict[str, Any]
    freshness: Freshness
    warning: str | None = None


class CashFlowItem(BaseModel):
    payment_date: str
    period_start: str | None = None
    coupon_amount: float | None = None
    principal_amount: float | None = None
    total_amount: float | None = None
    is_estimated: bool = False
    is_final: bool = False


class HistoryPoint(BaseModel):
    timestamp: str
    clean_price: float | None = None
    ytm: float | None = None
    volume: float | None = None
    turnover: float | None = None
    data_mode: str | None = None


class CalculatorRequest(BaseModel):
    amount: float = Field(gt=0, description="Сумма вложения в валюте выпуска")
    reinvest_coupons: bool = False

    @field_validator("amount")
    @classmethod
    def _sane(cls, value: float) -> float:
        if value > 1e12:
            raise ValueError("Сумма слишком велика")
        return value


class CompareRequest(BaseModel):
    identifiers: list[str] = Field(min_length=1, max_length=5)
    mode: str = "simple"

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in ("simple", "pro"):
            raise ValueError("mode must be 'simple' or 'pro'")
        return value
