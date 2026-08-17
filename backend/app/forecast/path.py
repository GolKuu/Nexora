"""Deterministic Monte Carlo price paths calibrated to model distributions."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import math
import random

from app.forecast.pipeline import _quantile


def kase_holidays(year: int) -> set[date]:
    """National KASE closures plus weekday substitution for weekend holidays."""
    fixed = {(1, 1), (1, 2), (1, 7), (3, 8), (3, 21), (3, 22), (3, 23),
             (5, 1), (5, 7), (5, 9), (7, 6), (8, 30), (10, 25), (12, 16)}
    holidays = {date(year, month, day) for month, day in fixed}
    occupied = set(holidays)
    for holiday in sorted(holidays):
        if holiday.weekday() >= 5:
            substitute = holiday + timedelta(days=1)
            while substitute.weekday() >= 5 or substitute in occupied:
                substitute += timedelta(days=1)
            occupied.add(substitute)
    return occupied


def _trading_days(start: datetime, count: int) -> list[datetime]:
    days: list[datetime] = []
    cursor = start
    while len(days) < count:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5 and cursor.date() not in kase_holidays(cursor.year):
            days.append(cursor)
    return days


class ForecastPathGenerator:
    def __init__(self, seed: int = 20260817, trajectories: int = 800):
        self.seed = seed
        self.trajectories = trajectories

    def generate(self, *, current_price: float, as_of: datetime, horizon: int,
                 return_distribution: list[float], annualized_volatility: float,
                 event_uncertainty: float = 0.0) -> list[dict]:
        rng = random.Random(self.seed + horizon)
        sigma = max(annualized_volatility, 1e-6) / math.sqrt(252)
        sigma *= 1.0 + max(0.0, event_uncertainty)
        paths: list[list[float]] = []
        for _ in range(self.trajectories):
            endpoint = rng.choice(return_distribution)
            shocks = [rng.gauss(0.0, sigma) for _ in range(horizon)]
            # Brownian bridge: preserve day-to-day uncertainty while exactly
            # calibrating each trajectory to an empirical model endpoint.
            correction = (endpoint - sum(shocks)) / horizon
            price = current_price
            path: list[float] = []
            for shock in shocks:
                price *= math.exp(shock + correction)
                path.append(price)
            paths.append(path)
        dates = _trading_days(as_of, horizon)
        output: list[dict] = []
        for i, date in enumerate(dates):
            values = [path[i] for path in paths]
            output.append({
                "date": date.isoformat(), "median": _quantile(values, 0.50),
                "q10": _quantile(values, 0.10), "q25": _quantile(values, 0.25),
                "q75": _quantile(values, 0.75), "q90": _quantile(values, 0.90),
            })
        return output


__all__ = ["ForecastPathGenerator", "kase_holidays"]
