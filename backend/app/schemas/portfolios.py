from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, model_validator


class PortfolioCreate(BaseModel):
    name: str = Field(default="Мой портфель", max_length=255)
    base_currency: str = Field(default="KZT", max_length=3)
    description: str | None = None


class PositionCreate(BaseModel):
    bond: str | None = Field(default=None, description="Обратная совместимость: облигация")
    stock: str | None = Field(default=None, description="Тикер, ISIN или id акции")
    instrument_type: str = Field(default="bond", pattern="^(bond|stock)$")
    quantity: float = Field(gt=0)
    purchase_clean_price: float | None = Field(default=None, gt=0, le=1000)
    purchase_price: float | None = Field(default=None, gt=0)
    purchase_date: date | None = None
    purchase_accrued_interest: float | None = Field(default=None, ge=0)
    fees: float | None = Field(default=None, ge=0)
    note: str | None = None


class PositionUpdate(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    purchase_clean_price: float | None = Field(default=None, gt=0, le=1000)
    purchase_price: float | None = Field(default=None, gt=0)
    purchase_date: date | None = None
    purchase_accrued_interest: float | None = Field(default=None, ge=0)
    fees: float | None = Field(default=None, ge=0)
    note: str | None = None

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class WatchlistCreate(BaseModel):
    bond: str | None = None
    stock: str | None = None
    instrument_type: str = Field(default="bond", pattern="^(bond|stock)$")
    note: str | None = None

    @model_validator(mode="after")
    def exactly_one_instrument(self):
        if (self.bond is None) == (self.stock is None):
            raise ValueError("Specify exactly one of bond or stock")
        if self.stock is not None:
            self.instrument_type = "stock"
        return self


class AlertCreate(BaseModel):
    bond: str | None = None
    stock: str | None = None
    instrument_type: str = Field(default="bond", pattern="^(bond|stock)$")
    kind: str = Field(pattern="^(price_below|price_above|ytm_above|ytm_below|score_above|coupon_date|maturity_date|pe_below|dividend_announced|financial_report|profit_change|score_change|company_news|price_approaches_support|support_broken|resistance_broken|golden_cross|death_cross|rsi_extreme|volume_spike|technical_risk_changed)$")
    threshold: float | None = None

    @model_validator(mode="after")
    def validate_alert(self):
        if (self.bond is None) == (self.stock is None):
            raise ValueError("Specify exactly one of bond or stock")
        if self.stock is not None:
            self.instrument_type = "stock"
            if self.kind in {"ytm_above", "ytm_below", "coupon_date", "maturity_date"}:
                raise ValueError("Bond alert kind cannot be used for a stock")
        elif self.kind in {"pe_below", "dividend_announced", "financial_report", "profit_change", "company_news"}:
            raise ValueError("Stock alert kind cannot be used for a bond")
        if self.kind in {"price_below", "price_above", "ytm_above", "ytm_below", "score_above", "pe_below"} and self.threshold is None:
            raise ValueError("This alert kind requires threshold")
        return self


class AlertUpdate(BaseModel):
    is_active: bool
