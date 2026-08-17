"""Result objects for the strict scoring system.

A score is never just a number here: it carries the components it was built
from, the red flags that dragged it down, the caps that bound it, and the data
limitations behind its confidence. The API serialises this object directly, so
an explanation can never drift away from the arithmetic that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.scoring.strict.scale import ComponentScore
from app.scoring.strict.versions import ModelVersion, band_for


@dataclass(frozen=True, slots=True)
class RedFlag:
    code: str
    severity: str            # critical | high | medium | low
    message: str
    penalty: float           # points removed from the base score
    evidence: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "penalty": round(self.penalty, 2),
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class AppliedCap:
    code: str
    ceiling: float
    reason: str
    binding: bool = False

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "ceiling": self.ceiling,
            "reason": self.reason,
            "binding": self.binding,
        }


@dataclass(slots=True)
class Confidence:
    value: float
    components: list[ComponentScore] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    version: str = ""

    def to_dict(self) -> dict:
        return {
            "value": round(self.value, 1),
            "version": self.version,
            "components": [c.to_dict() for c in self.components],
            "limitations": self.limitations,
        }


@dataclass(slots=True)
class StrictScore:
    """The full, self-explaining result of one scoring run."""

    kind: str                       # bond | stock | bank
    ticker: str | None
    version: ModelVersion
    calculated_at: datetime
    #: The valuation moment. Historical scores are computed as of this date and
    #: may only use information published on or before it.
    as_of: datetime | None

    base_score: float
    penalised_score: float
    final_score: float
    data_quality: float
    confidence: Confidence

    components: list[ComponentScore] = field(default_factory=list)
    red_flags: list[RedFlag] = field(default_factory=list)
    caps: list[AppliedCap] = field(default_factory=list)
    excluded_facts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def binding_caps(self) -> list[AppliedCap]:
        return [c for c in self.caps if c.binding]

    @property
    def rating_band(self) -> tuple[str, str]:
        return band_for(self.final_score)

    def component(self, code: str) -> ComponentScore | None:
        for c in self.components:
            if c.code == code:
                return c
        return None

    def component_score(self, code: str) -> float | None:
        component = self.component(code)
        return None if component is None else component.score

    def to_dict(self) -> dict:
        band_code, band_label = self.rating_band
        return {
            "kind": self.kind,
            "ticker": self.ticker,
            "version": self.version.to_dict(),
            "calculated_at": self.calculated_at.isoformat(),
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "final_score": round(self.final_score, 1),
            "base_score": round(self.base_score, 1),
            "penalised_score": round(self.penalised_score, 1),
            "band": band_code,
            "band_label": band_label,
            "data_quality": round(self.data_quality, 1),
            "confidence": self.confidence.to_dict(),
            "components": [c.to_dict() for c in self.components],
            "red_flags": [f.to_dict() for f in self.red_flags],
            "caps": [c.to_dict() for c in self.caps],
            "binding_caps": [c.to_dict() for c in self.binding_caps],
            "excluded_facts": self.excluded_facts,
            "notes": self.notes,
        }
