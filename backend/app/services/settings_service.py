"""User settings, including the inflation preferences.

Anonymous visitors get a full settings row keyed by a client token, so nobody
has to register just to switch on "после инфляции".
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.enums import InflationSource, RiskProfile, UiMode
from app.core.errors import ValidationError
from app.models.user import UserSettings
from app.providers.inflation import get_inflation
from app.repositories.settings import SettingsRepository

DEFAULTS = {
    "inflation_enabled": True,
    "inflation_source": InflationSource.AUTOMATIC.value,
    "manual_inflation_rate": None,
    "show_real_return": True,
    "base_currency": "KZT",
    "ui_mode": UiMode.SIMPLE.value,
    "risk_profile": RiskProfile.BALANCED.value,
    "theme": "system",
    "remember_calculator_amount": True,
    "calculator_amount": None,
    "language": "ru",
    "conservative_missing_data_mode": True,
    "news_enabled": True,
    "kase_news_enabled": True,
    "external_news_enabled": True,
    "chart_news_markers_enabled": True,
    "forecast_enabled": True,
    "uncertainty_intervals_enabled": True,
    "show_dcf_explanation": True,
    "show_dcf_confidence": True,
    "show_dcf_scenario_differences": True,
    "default_chart_range": "1y",
}

_ALLOWED = {
    "inflation_source": {e.value for e in InflationSource},
    "ui_mode": {e.value for e in UiMode},
    "risk_profile": {e.value for e in RiskProfile},
    "theme": {"light", "dark", "system"},
    "language": {"ru", "kk", "en"},
    "default_chart_range": {"1d", "5d", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "max"},
}


class SettingsService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = SettingsRepository(session)

    def get(self, *, user_id: int | None, token: str | None) -> dict:
        row = self.repo.get(user_id=user_id, token=token)
        return self.serialize(row)

    def get_or_create(self, *, user_id: int | None, token: str | None) -> UserSettings:
        return self.repo.get_or_create(user_id=user_id, token=token)

    def update(self, *, user_id: int | None, token: str | None, values: dict) -> dict:
        for key, allowed in _ALLOWED.items():
            if key in values and values[key] is not None and values[key] not in allowed:
                raise ValidationError(
                    f"Недопустимое значение {key}={values[key]}",
                    details={"allowed": sorted(allowed)},
                )
        if values.get("inflation_source") == InflationSource.MANUAL.value:
            rate = values.get("manual_inflation_rate")
            if rate is None:
                raise ValidationError(
                    "Для ручной инфляции нужно указать manual_inflation_rate."
                )
            if not -0.5 <= rate <= 2.0:
                raise ValidationError(
                    "manual_inflation_rate должен быть в диапазоне от -0.5 до 2.0 (доля, не проценты)."
                )
        row = self.repo.get_or_create(user_id=user_id, token=token)
        self.repo.update(row, values)
        return self.serialize(row)

    def serialize(self, row: UserSettings | None) -> dict:
        if row is None:
            return {**DEFAULTS, "persisted": False}
        return {
            "persisted": True,
            "inflation_enabled": row.inflation_enabled,
            "inflation_source": row.inflation_source,
            "manual_inflation_rate": row.manual_inflation_rate,
            "show_real_return": row.show_real_return,
            "base_currency": row.base_currency,
            "ui_mode": row.ui_mode,
            "risk_profile": row.risk_profile,
            "theme": row.theme,
            "remember_calculator_amount": row.remember_calculator_amount,
            "calculator_amount": row.calculator_amount,
            "language": row.language,
            "conservative_missing_data_mode": row.conservative_missing_data_mode,
            "news_enabled": row.news_enabled,
            "kase_news_enabled": row.kase_news_enabled,
            "external_news_enabled": row.external_news_enabled,
            "chart_news_markers_enabled": row.chart_news_markers_enabled,
            "forecast_enabled": row.forecast_enabled,
            "uncertainty_intervals_enabled": row.uncertainty_intervals_enabled,
            "show_dcf_explanation": row.show_dcf_explanation,
            "show_dcf_confidence": row.show_dcf_confidence,
            "show_dcf_scenario_differences": row.show_dcf_scenario_differences,
            "default_chart_range": row.default_chart_range,
        }

    def effective_inflation(self, settings_dict: dict, horizon_years: float | None = None) -> dict:
        """What inflation rate the app will actually use, and where it came from."""
        if not settings_dict.get("inflation_enabled", True):
            return {"enabled": False, "rate": None, "source": None}
        reading = get_inflation(
            self.session,
            source=settings_dict.get("inflation_source", "automatic"),
            manual_rate=settings_dict.get("manual_inflation_rate"),
            horizon_years=horizon_years,
        )
        if reading is None:
            return {
                "enabled": True,
                "rate": None,
                "source": None,
                "note": "Данные по инфляции отсутствуют - реальная доходность не рассчитывается.",
            }
        return {
            "enabled": True,
            "rate": reading.annual_rate,
            "rate_pct": round(reading.annual_rate * 100, 2),
            "source": reading.source,
            "kind": reading.kind,
            "period_end": reading.period_end.isoformat() if reading.period_end else None,
            "source_url": reading.source_url,
            "note": reading.note,
        }
