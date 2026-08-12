"""Portfolio aggregation and weighted scoring."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PositionInput:
    market_value: float | None
    ytm: float | None = None
    modified_duration: float | None = None


def _weighted_average(
    pairs: Sequence[tuple[float, float]]
) -> float | None:
    """pairs of (weight, value); weights are renormalised over what exists."""
    total_weight = sum(w for w, _ in pairs if w > 0)
    if total_weight <= 0:
        return None
    return sum(w * v for w, v in pairs if w > 0) / total_weight


def calculate_portfolio_ytm(
    positions: Sequence[PositionInput],
) -> float | None:
    """Market-value weighted yield.

    This is the standard approximation, not an internal rate of return on the
    combined cash-flow stream; positions with an unknown YTM are excluded from
    both the numerator and the denominator rather than treated as zero.
    """
    pairs = [
        (p.market_value, p.ytm)
        for p in positions
        if p.market_value and p.market_value > 0 and p.ytm is not None
    ]
    return _weighted_average(pairs)


def calculate_portfolio_duration(
    positions: Sequence[PositionInput],
) -> float | None:
    """Market-value weighted modified duration."""
    pairs = [
        (p.market_value, p.modified_duration)
        for p in positions
        if p.market_value and p.market_value > 0 and p.modified_duration is not None
    ]
    return _weighted_average(pairs)


def calculate_weighted_score(
    components: Sequence[tuple[str, float | None, float]],
) -> dict[str, float] | None:
    """Blend 0-100 sub-scores into one 0-100 score.

    ``components`` are ``(code, value, weight)``. Unavailable components are
    dropped and the remaining weights are renormalised, so a missing input
    never silently drags a score toward zero. ``coverage`` reports how much of
    the intended weight was actually available.
    """
    available = [(c, v, w) for c, v, w in components if v is not None and w > 0]
    intended_weight = sum(w for _, _, w in components if w > 0)
    if not available or intended_weight <= 0:
        return None
    actual_weight = sum(w for _, _, w in available)
    value = sum(v * w for _, v, w in available) / actual_weight
    return {
        "value": max(0.0, min(100.0, value)),
        "coverage": actual_weight / intended_weight,
    }
