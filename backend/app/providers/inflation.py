"""Inflation providers.

The real return shown to the user is only as good as the inflation number
behind it, so the source is always recorded and always shown.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import InflationSource
from app.models.macro import InflationData


@dataclass(slots=True)
class InflationReading:
    annual_rate: float
    source: str
    kind: str
    period_end: date | None = None
    horizon_years: float | None = None
    source_url: str | None = None
    fetched_at: datetime | None = None
    note: str | None = None


class InflationProvider(abc.ABC):
    kind: str = "abstract"

    @abc.abstractmethod
    def get_rate(self, *, horizon_years: float | None = None) -> InflationReading | None:
        """Annual inflation as a decimal, or None when genuinely unknown."""


class OfficialInflationProvider(InflationProvider):
    """Latest published official CPI, read from the database."""

    kind = InflationSource.OFFICIAL.value

    def __init__(self, session: Session, country: str = "KZ"):
        self.session = session
        self.country = country

    def get_rate(self, *, horizon_years: float | None = None) -> InflationReading | None:
        row = self.session.execute(
            select(InflationData)
            .where(
                InflationData.country == self.country,
                InflationData.kind == "official",
            )
            .order_by(InflationData.period_end.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return InflationReading(
            annual_rate=row.annual_rate,
            source=row.source or "official",
            kind="official",
            period_end=row.period_end,
            source_url=row.source_url,
            fetched_at=row.fetched_at,
            note=row.note,
        )


class ForecastInflationProvider(InflationProvider):
    """Published forecast for the horizon closest to the investment period."""

    kind = InflationSource.FORECAST.value

    def __init__(self, session: Session, country: str = "KZ"):
        self.session = session
        self.country = country

    def get_rate(self, *, horizon_years: float | None = None) -> InflationReading | None:
        rows = list(
            self.session.execute(
                select(InflationData)
                .where(
                    InflationData.country == self.country,
                    InflationData.kind == "forecast",
                )
                .order_by(InflationData.period_end.desc())
            ).scalars()
        )
        if not rows:
            return None
        if horizon_years is not None:
            rows.sort(key=lambda r: abs((r.horizon_years or 1.0) - horizon_years))
        row = rows[0]
        return InflationReading(
            annual_rate=row.annual_rate,
            source=row.source or "forecast",
            kind="forecast",
            period_end=row.period_end,
            horizon_years=row.horizon_years,
            source_url=row.source_url,
            fetched_at=row.fetched_at,
            note=row.note,
        )


class ManualInflationProvider(InflationProvider):
    """The rate the user typed in themselves."""

    kind = InflationSource.MANUAL.value

    def __init__(self, rate: float | None):
        self.rate = rate

    def get_rate(self, *, horizon_years: float | None = None) -> InflationReading | None:
        if self.rate is None:
            return None
        return InflationReading(
            annual_rate=self.rate,
            source="user",
            kind="manual",
            fetched_at=datetime.now(timezone.utc),
            note="Значение задано пользователем вручную.",
        )


def resolve_inflation_provider(
    session: Session,
    *,
    source: str,
    manual_rate: float | None = None,
    country: str = "KZ",
) -> list[InflationProvider]:
    """Ordered providers to try for the requested source setting."""
    if source == InflationSource.MANUAL.value:
        return [ManualInflationProvider(manual_rate)]
    if source == InflationSource.FORECAST.value:
        return [
            ForecastInflationProvider(session, country),
            OfficialInflationProvider(session, country),
        ]
    if source == InflationSource.OFFICIAL.value:
        return [OfficialInflationProvider(session, country)]
    # automatic: a forecast matched to the investment horizon is the better
    # basis for a multi-year real return; fall back to the last official print.
    return [
        ForecastInflationProvider(session, country),
        OfficialInflationProvider(session, country),
    ]


def get_inflation(
    session: Session,
    *,
    source: str = InflationSource.AUTOMATIC.value,
    manual_rate: float | None = None,
    horizon_years: float | None = None,
    country: str = "KZ",
) -> InflationReading | None:
    for provider in resolve_inflation_provider(
        session, source=source, manual_rate=manual_rate, country=country
    ):
        reading = provider.get_rate(horizon_years=horizon_years)
        if reading is not None:
            return reading
    return None
