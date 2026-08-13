"""DataValidator - the gate between "the browser saw it" and "the DB stores it".

Nothing extracted by the browser, and nothing proposed by an AI planner, goes
into the database directly (§38). It arrives here as a candidate, and leaves as
either an accepted value or a rejection with a reason.

Three jobs:

* **source priority** (§29) - an official structured source beats page text,
  which beats a tooltip, which beats a visual impression. A visual reading
  never silently replaces a precise one.
* **cross-checking** (§30) - the same figure found in two places is compared,
  and a disagreement produces a warning, not a coin flip.
* **sanity** - a coupon of 900% or a maturity in 1970 is a parsing failure, and
  is refused rather than stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.browser.types import METHOD_CONFIDENCE, ExtractedValue, ExtractionMethod
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Lower is better. Mirrors §29 exactly.
METHOD_PRIORITY = {
    "official_api": 0,
    ExtractionMethod.TABLE.value: 1,
    ExtractionMethod.DOM.value: 1,
    ExtractionMethod.TOOLTIP.value: 2,
    ExtractionMethod.DOCUMENT.value: 3,
    ExtractionMethod.VISUAL.value: 4,
}

#: Plausible ranges for the fields we accept from a page. A value outside its
#: range means the parse went wrong, not that the market did something exotic.
RANGES: dict[str, tuple[float, float]] = {
    "coupon_rate": (0.0, 1.5),          # decimal fraction: 0% - 150%
    "ytm": (-0.5, 3.0),
    "clean_price": (0.0, 100_000.0),    # percent of par, or absolute
    "dirty_price": (0.0, 100_000.0),
    "nominal": (0.0, 1e12),
    "issue_size": (0.0, 1e15),
    "outstanding_amount": (0.0, 1e15),
    "coupon_frequency": (0.0, 366.0),
    "days_to_maturity": (-40_000.0, 40_000.0),
}

#: Fields no visual analysis may ever supply (§13, §14).
NEVER_FROM_VISUAL = {
    "coupon_rate", "ytm", "clean_price", "dirty_price", "nominal",
    "issue_size", "outstanding_amount", "maturity_date", "issue_date",
    "next_coupon_date", "isin",
}

_MIN_DATE = date(1990, 1, 1)
_MAX_DATE = date(2100, 1, 1)


@dataclass(slots=True)
class ValidationIssue:
    field: str
    severity: str  # error | warning
    message: str
    values: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "field": self.field,
            "severity": self.severity,
            "message": self.message,
            "values": self.values,
        }


@dataclass(slots=True)
class ValidationResult:
    accepted: dict[str, ExtractedValue] = field(default_factory=dict)
    rejected: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "error" for issue in self.rejected)

    def value(self, name: str):
        item = self.accepted.get(name)
        return None if item is None else item.normalized

    def as_dict(self) -> dict:
        return {
            "accepted": {k: v.as_dict() for k, v in self.accepted.items()},
            "rejected": [i.as_dict() for i in self.rejected],
            "warnings": [i.as_dict() for i in self.warnings],
            "accepted_count": len(self.accepted),
        }


class DataValidator:
    version = "1.0.0"

    def validate(self, candidates: list[ExtractedValue]) -> ValidationResult:
        """Resolve competing candidates per field into at most one value each."""
        result = ValidationResult()
        by_field: dict[str, list[ExtractedValue]] = {}
        for candidate in candidates:
            by_field.setdefault(candidate.field, []).append(candidate)

        for name, group in by_field.items():
            usable = []
            for candidate in group:
                problem = self._reject_reason(candidate)
                if problem is None:
                    usable.append(candidate)
                else:
                    result.rejected.append(
                        ValidationIssue(name, "error", problem, [candidate.as_dict()])
                    )
            if not usable:
                continue

            usable.sort(key=lambda c: (METHOD_PRIORITY.get(c.method, 9), -c.confidence))
            winner = usable[0]

            conflict = self._conflict(name, usable)
            if conflict is not None:
                result.warnings.append(conflict)
                # A conflict lowers how much we trust the winner, but the
                # winner is still chosen by source priority - never at random.
                winner.confidence = min(winner.confidence, 0.6)
                winner.warnings.append(conflict.message)
            result.accepted[name] = winner
        return result

    # -- checks ------------------------------------------------------------

    def _reject_reason(self, candidate: ExtractedValue) -> str | None:
        name, value = candidate.field, candidate.normalized
        if value is None:
            return "normalized value is empty"
        if candidate.method == ExtractionMethod.VISUAL.value and name in NEVER_FROM_VISUAL:
            return (
                f"'{name}' may not come from visual interpretation; "
                "read it from the DOM, a table or a tooltip instead"
            )
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            bounds = RANGES.get(name)
            if bounds and not bounds[0] <= float(value) <= bounds[1]:
                return f"{value} is outside the plausible range {bounds} for '{name}'"
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date) and not _MIN_DATE <= value <= _MAX_DATE:
            return f"date {value.isoformat()} is implausible for '{name}'"
        return None

    def _conflict(self, name: str, group: list[ExtractedValue]) -> ValidationIssue | None:
        """Compare the same figure found in several places (§30)."""
        if len(group) < 2:
            return None
        distinct: list[ExtractedValue] = []
        for candidate in group:
            if not any(_equivalent(candidate.normalized, other.normalized) for other in distinct):
                distinct.append(candidate)
        if len(distinct) < 2:
            return None
        message = (
            f"'{name}' differs between sources: "
            + "; ".join(
                f"{c.normalized!r} via {c.method}"
                + (f" ({c.source.section})" if c.source and c.source.section else "")
                for c in distinct
            )
        )
        logger.info("browser cross-check conflict: %s", message)
        return ValidationIssue(name, "warning", message, [c.as_dict() for c in distinct])


def _equivalent(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        scale = max(abs(float(a)), abs(float(b)), 1e-9)
        return abs(float(a) - float(b)) / scale < 1e-4
    if isinstance(a, datetime) and isinstance(b, datetime):
        return a.date() == b.date()
    return a == b


def make_value(
    field_name: str,
    raw: str | None,
    normalized,
    *,
    method: str = ExtractionMethod.DOM.value,
    source=None,
    label: str | None = None,
    unit: str | None = None,
    confidence: float | None = None,
) -> ExtractedValue:
    """Build an ExtractedValue with the confidence its method has earned (§31)."""
    return ExtractedValue(
        field=field_name,
        raw=raw,
        normalized=normalized,
        unit=unit,
        method=method,
        confidence=confidence if confidence is not None else METHOD_CONFIDENCE.get(method, 0.5),
        source=source,
        label=label,
    )
