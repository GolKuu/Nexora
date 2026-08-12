"""Mapping raw financial quantities onto the 0-100 scale.

Every mapping is explicit and versioned with the scoring model. A ``None`` in
means a ``None`` out - a missing metric must never be scored as if it were bad
news or good news.
"""

from __future__ import annotations

from collections.abc import Sequence

SCORE_MIN = 0.0
SCORE_MAX = 100.0


def clamp(value: float, low: float = SCORE_MIN, high: float = SCORE_MAX) -> float:
    return max(low, min(high, value))


def linear(
    value: float | None,
    worst: float,
    best: float,
) -> float | None:
    """Interpolate linearly between the value considered worst and best.

    ``worst`` may be greater than ``best`` for metrics where lower is better
    (e.g. Debt/EBITDA), which keeps every call site reading the same way.
    """
    if value is None:
        return None
    if worst == best:
        return None
    return clamp((value - worst) / (best - worst) * 100.0)


def banded(
    value: float | None, bands: Sequence[tuple[float, float]]
) -> float | None:
    """Piecewise-linear mapping through ``(raw, score)`` breakpoints.

    Breakpoints must be sorted ascending by raw value. Values outside the range
    clamp to the nearest endpoint score.
    """
    if value is None or not bands:
        return None
    if value <= bands[0][0]:
        return clamp(bands[0][1])
    if value >= bands[-1][0]:
        return clamp(bands[-1][1])
    for (x0, y0), (x1, y1) in zip(bands, bands[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return clamp(y1)
            ratio = (value - x0) / (x1 - x0)
            return clamp(y0 + ratio * (y1 - y0))
    return clamp(bands[-1][1])


def average(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


#: S&P/Fitch-style ladder mapped to a numeric grade (1 == AAA, 21 == D).
RATING_GRADES: dict[str, int] = {
    "AAA": 1,
    "AA+": 2,
    "AA": 3,
    "AA-": 4,
    "A+": 5,
    "A": 6,
    "A-": 7,
    "BBB+": 8,
    "BBB": 9,
    "BBB-": 10,
    "BB+": 11,
    "BB": 12,
    "BB-": 13,
    "B+": 14,
    "B": 15,
    "B-": 16,
    "CCC+": 17,
    "CCC": 18,
    "CCC-": 19,
    "CC": 20,
    "C": 20,
    "D": 21,
}


def rating_to_grade(rating: str | None) -> int | None:
    if not rating:
        return None
    return RATING_GRADES.get(rating.strip().upper())


def grade_to_score(grade: int | None) -> float | None:
    """Investment grade (<=10) lands at 60+, speculative below."""
    return banded(
        None if grade is None else float(grade),
        [(1.0, 100.0), (6.0, 88.0), (10.0, 65.0), (13.0, 45.0), (16.0, 25.0), (21.0, 0.0)],
    )
