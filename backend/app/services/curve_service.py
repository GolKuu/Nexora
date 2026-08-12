"""Risk-free curve lookup with linear interpolation between published tenors."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.macro import YieldCurve


def get_risk_free_rate(
    session: Session,
    tenor_years: float | None,
    *,
    curve_code: str = "KZ_GOV",
    currency: str = "KZT",
    as_of: date | None = None,
) -> float | None:
    """Interpolated government yield for a tenor. None when the curve is absent."""
    if tenor_years is None or tenor_years <= 0:
        return None
    latest_date = session.execute(
        select(YieldCurve.as_of_date)
        .where(YieldCurve.curve_code == curve_code, YieldCurve.currency == currency)
        .order_by(YieldCurve.as_of_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_date is None:
        return None
    points = list(
        session.execute(
            select(YieldCurve)
            .where(
                YieldCurve.curve_code == curve_code,
                YieldCurve.currency == currency,
                YieldCurve.as_of_date == (as_of or latest_date),
            )
            .order_by(YieldCurve.tenor_years)
        ).scalars()
    )
    if not points:
        return None
    if tenor_years <= points[0].tenor_years:
        return points[0].yield_rate
    if tenor_years >= points[-1].tenor_years:
        return points[-1].yield_rate
    for left, right in zip(points, points[1:]):
        if left.tenor_years <= tenor_years <= right.tenor_years:
            span = right.tenor_years - left.tenor_years
            if span == 0:
                return left.yield_rate
            ratio = (tenor_years - left.tenor_years) / span
            return left.yield_rate + ratio * (right.yield_rate - left.yield_rate)
    return None
