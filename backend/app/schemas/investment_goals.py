from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class GoalPlanRequest(BaseModel):
    starting_capital: float = Field(gt=0, le=10**13)
    target_type: Literal["FINAL_VALUE", "PROFIT"] = "FINAL_VALUE"
    target_amount: float = Field(gt=0, le=10**14)
    horizon_months: int = Field(ge=1, le=600)
    monthly_contribution: float = Field(default=0, ge=0, le=10**12)
    risk_profile: Literal["conservative", "balanced", "growth", "income"] = "balanced"
    currency: Literal["KZT"] = "KZT"
    excluded_instruments: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_target(self):
        final_value = (
            self.starting_capital + self.target_amount
            if self.target_type == "PROFIT"
            else self.target_amount
        )
        if final_value <= 0:
            raise ValueError("Итоговая цель должна быть больше нуля.")
        return self


class PlanPositionEdit(BaseModel):
    ticker: str = Field(min_length=1, max_length=64)
    quantity: float = Field(ge=0)


class PlanEditRequest(BaseModel):
    positions: list[PlanPositionEdit] = Field(min_length=1, max_length=100)


class ExecutePositionRequest(BaseModel):
    actual_quantity: float = Field(gt=0)
    actual_price: float = Field(gt=0)
    actual_commission: float = Field(default=0, ge=0)
    execution_date: date

