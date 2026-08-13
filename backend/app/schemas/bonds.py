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
    credit_score: float | None = None
    liquidity_score: float | None = None
    growth_score: float | None = None
    hold_score: float | None = None
    trade_score: float | None = None
    data_quality_score: float | None = None
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
    identifiers: list[str] = Field(min_length=1, max_length=10)
    mode: str = "simple"
    #: When given, every bond is compared on the same amount of money (§38).
    amount: float | None = Field(default=None, gt=0)
    inflation_enabled: bool = True

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in ("simple", "pro"):
            raise ValueError("mode must be 'simple' or 'pro'")
        return value


class CommissionSpec(BaseModel):
    """Broker commission. ``percent`` is charged on the full purchase amount."""

    type: str = Field(default="percent", pattern="^(percent|fixed|none)$")
    value: float = Field(default=0.0, ge=0)


class InvestmentCalculationRequest(BaseModel):
    """Request body of ``POST /bonds/{identifier}/investment-calculation`` (§16)."""

    mode: str = Field(default="amount", pattern="^(amount|quantity)$")
    amount: float = Field(gt=0, le=1e13, description="Сумма вложения")
    currency: str = "KZT"
    commission: CommissionSpec = Field(default_factory=CommissionSpec)
    inflation_enabled: bool = True
    #: maturity | date
    exit_mode: str = Field(default="maturity", pattern="^(maturity|date)$")
    exit_date: str | None = None
    scenario: str = Field(default="base", pattern="^(bad|base|good)$")

    @field_validator("exit_date")
    @classmethod
    def _exit_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from datetime import date as _date

        _date.fromisoformat(value)  # raises for a malformed date
        return value


class InvestmentCashFlow(BaseModel):
    date: str
    type: str
    coupon_amount: float | None = None
    principal_amount: float | None = None
    total_amount: float | None = None
    is_estimated: bool = False


class InvestmentCalculationResponse(BaseModel):
    """Response of the investment calculator (§17).

    Every monetary field is in the bond's own currency. Percentages are already
    multiplied by 100. ``null`` always means "not known", never "zero".
    """

    bond_identifier: str
    currency: str = "KZT"
    input_amount: float
    quantity: float = 0

    unit_clean_price: float | None = None
    unit_dirty_price: float | None = None
    accrued_interest_per_bond: float | None = None

    principal_cost: float = 0.0
    accrued_interest_total: float = 0.0
    commission: float = 0.0

    total_purchase_cost: float = 0.0
    cash_remaining: float = 0.0
    minimum_required_amount: float | None = None

    coupon_income: float = 0.0
    principal_repayment: float = 0.0
    estimated_price_return: float | None = None

    total_profit: float | None = None
    total_cash_received: float | None = None

    total_return_percent: float | None = None
    annualized_return_percent: float | None = None

    real_profit: float | None = None
    real_return_percent: float | None = None
    real_annualized_return_percent: float | None = None
    inflation_rate_percent: float | None = None
    inflation_source: str | None = None

    holding_period_years: float | None = None
    #: ask | last | bid - which quote the purchase was priced from (§13).
    price_basis: str | None = None
    scenario: str = "base"
    exit_mode: str = "maturity"
    exit_date: str | None = None

    cashflows: list[InvestmentCashFlow] = Field(default_factory=list)

    liquidity_warning: str | None = None
    warnings: list[str] = Field(default_factory=list)

    data_timestamp: str | None = None
    source: str | None = None
    source_url: str | None = None
    data_mode: str | None = None


class RecommendRequest(BaseModel):
    """Request body of ``POST /bonds/recommend`` (§34)."""

    amount: float = Field(gt=0, le=1e13)
    currency: str = "KZT"
    max_maturity_years: float | None = Field(default=None, gt=0)
    min_maturity_years: float | None = Field(default=None, ge=0)
    profile: str = Field(default="balanced", pattern="^(conservative|balanced|aggressive)$")
    inflation_enabled: bool = True
    limit: int = Field(default=5, ge=1, le=25)
    commission: CommissionSpec = Field(default_factory=CommissionSpec)


class RecommendItem(BaseModel):
    ticker: str
    isin: str | None = None
    issuer: str | None = None
    currency: str
    maturity_date: str | None = None
    years_to_maturity: float | None = None
    coupon_rate_pct: float | None = None

    ytm_pct: float | None = None
    real_ytm_pct: float | None = None

    credit_score: float | None = None
    liquidity_score: float | None = None
    growth_score: float | None = None
    investment_score: float | None = None
    hold_score: float | None = None
    data_quality_score: float | None = None

    #: Machine-readable justifications, ranked. The LLM explains these; it
    #: never produces or reorders them (§35).
    reason_codes: list[str] = Field(default_factory=list)
    investment_calculation: InvestmentCalculationResponse | None = None
    data_timestamp: str | None = None
    data_mode: str | None = None


class RecommendResponse(BaseModel):
    items: list[RecommendItem]
    amount: float
    currency: str
    profile: str
    #: Bonds that passed the hard filters before ranking.
    candidates_considered: int = 0
    ranking_version: str | None = None
    warning: str | None = None

