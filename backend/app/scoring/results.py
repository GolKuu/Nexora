"""Score result value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ComponentResult:
    code: str
    label: str
    value: float | None          # 0..100
    weight: float
    raw_value: float | None = None
    raw_unit: str | None = None
    explanation: str | None = None

    @property
    def available(self) -> bool:
        return self.value is not None

    @property
    def contribution(self) -> float | None:
        if self.value is None:
            return None
        return self.value * self.weight

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "label": self.label,
            "value": self.value,
            "weight": self.weight,
            "contribution": self.contribution,
            "raw_value": self.raw_value,
            "raw_unit": self.raw_unit,
            "available": self.available,
            "explanation": self.explanation,
        }


@dataclass(slots=True)
class ScoreResult:
    kind: str
    value: float | None
    version: str
    calculated_at: datetime
    confidence: float | None = None
    components: list[ComponentResult] = field(default_factory=list)
    inputs: dict = field(default_factory=dict)
    notes: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "value": self.value,
            "version": self.version,
            "calculated_at": self.calculated_at.isoformat(),
            "confidence": self.confidence,
            "components": [c.to_dict() for c in self.components],
            "inputs": self.inputs,
            "notes": self.notes,
        }
