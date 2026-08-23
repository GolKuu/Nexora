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
    conservative_missing_data_mode: bool = True
    news_enabled: bool = True
    kase_news_enabled: bool = True
    external_news_enabled: bool = True
    chart_news_markers_enabled: bool = True
    forecast_enabled: bool = True
    uncertainty_intervals_enabled: bool = True
    default_chart_range: str = "1y"


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
    conservative_missing_data_mode: bool | None = None
    news_enabled: bool | None = None
    kase_news_enabled: bool | None = None
    external_news_enabled: bool | None = None
    chart_news_markers_enabled: bool | None = None
    forecast_enabled: bool | None = None
    uncertainty_intervals_enabled: bool | None = None
    default_chart_range: str | None = None

    def changes(self) -> dict:
        return {k: v for k, v in self.model_dump().items() if v is not None}
