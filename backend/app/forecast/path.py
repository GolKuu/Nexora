"""Deterministic Monte Carlo price paths calibrated to model distributions."""

from __future__ import annotations

from datetime import datetime
import math
import random

from app.forecast.calendar import kase_holidays, trading_days
from app.forecast.pipeline import _quantile


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
        dates = trading_days(as_of, horizon)
        output: list[dict] = []
        for i, date in enumerate(dates):
            values = [path[i] for path in paths]
            output.append({
                "date": date.isoformat(), "median": _quantile(values, 0.50),
                "q10": _quantile(values, 0.10), "q25": _quantile(values, 0.25),
                "q75": _quantile(values, 0.75), "q90": _quantile(values, 0.90),
            })
        return output


__all__ = ["ForecastPathGenerator", "kase_holidays", "trading_days"]
