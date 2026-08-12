from __future__ import annotations

from pydantic import BaseModel, Field


class SettingsResponse(BaseModel):
    persisted: bool = False
    inflation_enabled: bool = True
    inflation_source: str = "automatic"
    manual_inflation_rate: float | None = None
    show_real_return: bool = True
    base_currency: str = "KZT"
    ui_mode: str = "simple"
    risk_profile: str = "balanced"
    theme: str = "system"
    remember_calculator_amount: bool = True
    calculator_amount: float | None = None
    language: str = "ru"


class SettingsUpdate(BaseModel):
    inflation_enabled: bool | None = None
    inflation_source: str | None = None
    manual_inflation_rate: float | None = Field(default=None, ge=-0.5, le=2.0)
    show_real_return: bool | None = None
    base_currency: str | None = None
    ui_mode: str | None = None
    risk_profile: str | None = None
    theme: str | None = None
    remember_calculator_amount: bool | None = None
    calculator_amount: float | None = Field(default=None, ge=0)
    language: str | None = None

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}
