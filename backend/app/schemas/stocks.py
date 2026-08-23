from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class CommissionRequest(BaseModel):
    type: str = "percent"
    value: float = Field(default=0.0, ge=0)

    @field_validator("type")
    @classmethod
    def valid_type(cls, value: str) -> str:
        if value not in {"percent", "fixed"}:
            raise ValueError("commission.type must be percent or fixed")
        return value


class StockInvestmentRequest(BaseModel):
    mode: str = Field(default="amount", pattern="^(amount|quantity)$")
    amount: float | None = Field(default=None, gt=0)
    quantity: float | None = Field(default=None, gt=0)
    currency: str = "KZT"
    commission: CommissionRequest = Field(default_factory=CommissionRequest)
    scenario: str = "base"
    target_period_months: int = Field(default=12, ge=1, le=120)

    @model_validator(mode="after")
    def validate_input(self):
        if self.mode == "amount" and self.amount is None:
            raise ValueError("amount is required in amount mode")
        if self.mode == "quantity" and self.quantity is None:
            raise ValueError("quantity is required in quantity mode")
        return self


class StockRecommendRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: str = "KZT"
    profile: str = "balanced"
    limit: int = Field(default=5, ge=1, le=20)
    min_dividend_yield: float | None = None
    max_pe: float | None = None
    min_roe: float | None = None
    min_quality_score: float | None = None
    min_liquidity_score: float | None = None
    max_net_debt_to_equity: float | None = None
    sector: str | None = None


class StockCompareRequest(BaseModel):
    identifiers: list[str] = Field(min_length=2, max_length=10)
    amount: float | None = Field(default=None, gt=0)
    scenario: str = "base"


class UniversalSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)


class CrossAssetItem(BaseModel):
    identifier: str = Field(min_length=1)
    instrument_type: str = Field(pattern="^(bond|stock)$")


class CrossAssetCompareRequest(BaseModel):
    instruments: list[CrossAssetItem] = Field(min_length=2, max_length=10)
