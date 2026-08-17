"""Deterministic 0-100 mappings.

Every threshold in the strict scoring system goes through this module. The
mappings are step functions and piecewise ramps - no smoothing, no randomness,
no hidden state - so the same raw value always produces the same normalized
score for a given model version.

A ``None`` in always means a ``None`` out. Missing data is handled by the
aggregator (with an explicit conservative prior), never by silently scoring a
missing metric as zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

SCORE_MIN = 0.0
SCORE_MAX = 100.0

#: Score assigned to the weight of a component we could not measure.
#: Deliberately below the mid point: an unmeasured risk metric must never make
#: an instrument look better than one where the same metric was measured and
#: came back merely average.
MISSING_PRIOR = 40.0


def clamp(value: float, low: float = SCORE_MIN, high: float = SCORE_MAX) -> float:
    return max(low, min(high, value))


def is_number(value: float | None) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))


def step_low_better(
    value: float | None,
    steps: Sequence[tuple[float, float]],
    worst: float,
) -> float | None:
    """Score a metric where a lower raw value is better.

    ``steps`` are ``(upper_bound, score)`` pairs sorted ascending; the first
    bound the value falls strictly below wins. Anything at or above the last
    bound scores ``worst``.

        step_low_better(2.4, [(1, 100), (2, 90), (3, 75)], worst=15) -> 75
    """
    if not is_number(value):
        return None
    for bound, score in steps:
        if value < bound:
            return clamp(score)
    return clamp(worst)


def step_high_better(
    value: float | None,
    steps: Sequence[tuple[float, float]],
    worst: float,
) -> float | None:
    """Score a metric where a higher raw value is better.

    ``steps`` are ``(lower_bound, score)`` pairs sorted descending; the first
    bound the value reaches wins.

        step_high_better(6.0, [(8, 100), (5, 90), (3, 75)], worst=0) -> 90
    """
    if not is_number(value):
        return None
    for bound, score in steps:
        if value >= bound:
            return clamp(score)
    return clamp(worst)


def ramp(
    value: float | None, points: Sequence[tuple[float, float]]
) -> float | None:
    """Piecewise-linear interpolation through ``(raw, score)`` breakpoints.

    Breakpoints must be sorted ascending by raw value; values outside the range
    clamp to the nearest endpoint. Used where the spec asks for a continuous
    range ("1-2x = 15-35") rather than a flat band.
    """
    if not is_number(value) or not points:
        return None
    if value <= points[0][0]:
        return clamp(points[0][1])
    if value >= points[-1][0]:
        return clamp(points[-1][1])
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= value <= x1:
            if x1 == x0:
                return clamp(y1)
            return clamp(y0 + (value - x0) / (x1 - x0) * (y1 - y0))
    return clamp(points[-1][1])


def mean(values: Sequence[float | None]) -> float | None:
    present = [v for v in values if is_number(v)]
    if not present:
        return None
    return sum(present) / len(present)


def blend(parts: Sequence[tuple[float | None, float]]) -> float | None:
    """Weighted mean over the parts that are actually present."""
    present = [(v, w) for v, w in parts if is_number(v) and w > 0]
    if not present:
        return None
    total = sum(w for _, w in present)
    return sum(v * w for v, w in present) / total


def cap_at(value: float | None, ceiling: float) -> float | None:
    if value is None:
        return None
    return min(value, ceiling)


# ---------------------------------------------------------------------------
# component container
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ComponentScore:
    """One measured contributor to a score, carrying its own audit trail."""

    code: str
    label: str
    score: float | None
    weight: float
    raw_value: float | None = None
    unit: str | None = None
    reason: str | None = None
    source: str | None = None
    as_of: datetime | None = None
    children: list["ComponentScore"] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.score is not None

    @property
    def contribution(self) -> float | None:
        if self.score is None:
            return None
        return round(self.score * self.weight, 4)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "score": None if self.score is None else round(self.score, 2),
            "weight": round(self.weight, 4),
            "contribution": self.contribution,
            "raw_value": self.raw_value,
            "unit": self.unit,
            "reason": self.reason,
            "source": self.source,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "available": self.available,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass(slots=True)
class Aggregate:
    value: float
    measured_value: float | None
    coverage: float
    missing_weight: float


def aggregate(
    components: Sequence[ComponentScore], *, missing_prior: float = MISSING_PRIOR
) -> Aggregate:
    """Combine weighted components without ever rewarding missing data.

    The measured components are averaged over their own weight; the weight of
    everything we could not measure is filled with ``missing_prior``. That keeps
    the arithmetic deterministic and makes a gap in the data a mild drag rather
    than a free pass or an automatic zero.
    """
    total_weight = sum(c.weight for c in components)
    if total_weight <= 0:
        return Aggregate(value=missing_prior, measured_value=None, coverage=0.0, missing_weight=1.0)

    measured = [c for c in components if c.available]
    measured_weight = sum(c.weight for c in measured)
    coverage = measured_weight / total_weight
    missing_weight = 1.0 - coverage

    if not measured:
        return Aggregate(
            value=clamp(missing_prior), measured_value=None, coverage=0.0, missing_weight=1.0
        )

    measured_value = sum((c.score or 0.0) * c.weight for c in measured) / measured_weight
    value = measured_value * coverage + missing_prior * missing_weight
    return Aggregate(
        value=clamp(value),
        measured_value=clamp(measured_value),
        coverage=round(coverage, 4),
        missing_weight=round(missing_weight, 4),
    )


# ---------------------------------------------------------------------------
# credit ratings
# ---------------------------------------------------------------------------

RATING_GRADES: dict[str, int] = {
    "AAA": 1, "AA+": 2, "AA": 3, "AA-": 4, "A+": 5, "A": 6, "A-": 7,
    "BBB+": 8, "BBB": 9, "BBB-": 10, "BB+": 11, "BB": 12, "BB-": 13,
    "B+": 14, "B": 15, "B-": 16, "CCC+": 17, "CCC": 18, "CCC-": 19,
    "CC": 20, "C": 20, "RD": 21, "D": 21,
}


def rating_to_grade(rating: str | None) -> int | None:
    if not rating:
        return None
    return RATING_GRADES.get(rating.strip().upper())


def rating_score(rating: str | None) -> float | None:
    """Investment grade (BBB- and better) lands at 65+, speculative below."""
    grade = rating_to_grade(rating)
    if grade is None:
        return None
    return ramp(
        float(grade),
        [(1.0, 100.0), (6.0, 90.0), (10.0, 68.0), (13.0, 46.0), (16.0, 26.0), (19.0, 10.0), (21.0, 0.0)],
    )


def rating_notches(before: str | None, after: str | None) -> int | None:
    """Positive when ``after`` is worse than ``before`` (a downgrade)."""
    a, b = rating_to_grade(before), rating_to_grade(after)
    if a is None or b is None:
        return None
    return b - a
