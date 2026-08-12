from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class PortfolioCreate(BaseModel):
    name: str = Field(default="Мой портфель", max_length=255)
    base_currency: str = Field(default="KZT", max_length=3)
    description: str | None = None


class PositionCreate(BaseModel):
    bond: str = Field(description="Тикер, ISIN или id облигации")
    quantity: float = Field(gt=0)
    purchase_clean_price: float | None = Field(default=None, gt=0, le=1000)
    purchase_date: date | None = None
    purchase_accrued_interest: float | None = Field(default=None, ge=0)
    fees: float | None = Field(default=None, ge=0)
    note: str | None = None


class PositionUpdate(BaseModel):
    quantity: float | None = Field(default=None, gt=0)
    purchase_clean_price: float | None = Field(default=None, gt=0, le=1000)
    purchase_date: date | None = None
    purchase_accrued_interest: float | None = Field(default=None, ge=0)
    fees: float | None = Field(default=None, ge=0)
    note: str | None = None

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}


class WatchlistCreate(BaseModel):
    bond: str
    note: str | None = None
