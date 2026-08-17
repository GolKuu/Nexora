"""Formula versions.

Every weight, threshold, cap and red-flag rule in this package belongs to a
named version. Changing any of them means minting a new version id here - the
old id keeps meaning exactly what it meant when the historical scores were
written, so stored scores are never silently re-interpreted.

Nothing in the codebase may rewrite a persisted score in place; a re-scored
instrument is a *new* snapshot carrying the new version id.
"""

from __future__ import annotations

from dataclasses import dataclass

BOND_SCORE_VERSION = "bond_score_v1"
STOCK_SCORE_VERSION = "stock_score_v1"
BANK_SCORE_VERSION = "bank_score_v1"
RED_FLAG_VERSION = "red_flags_v1"
CAP_VERSION = "score_caps_v1"
CONFIDENCE_VERSION = "confidence_v1"
DATA_QUALITY_VERSION = "data_quality_v1"


@dataclass(frozen=True, slots=True)
class ModelVersion:
    """The full set of formula ids a single score was produced with."""

    model: str
    red_flags: str = RED_FLAG_VERSION
    caps: str = CAP_VERSION
    confidence: str = CONFIDENCE_VERSION
    data_quality: str = DATA_QUALITY_VERSION

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "red_flags": self.red_flags,
            "caps": self.caps,
            "confidence": self.confidence,
            "data_quality": self.data_quality,
        }


BOND_MODEL = ModelVersion(model=BOND_SCORE_VERSION)
STOCK_MODEL = ModelVersion(model=STOCK_SCORE_VERSION)
BANK_MODEL = ModelVersion(model=BANK_SCORE_VERSION)


#: Score bands used everywhere the number is turned into words.
SCORE_BANDS: tuple[tuple[float, str, str], ...] = (
    (90.0, "exceptional", "Исключительно"),
    (75.0, "strong", "Сильно"),
    (60.0, "acceptable", "Приемлемо"),
    (40.0, "high_risk", "Высокий риск"),
    (0.0, "weak", "Слабо / спекулятивно"),
)


def band_for(score: float | None) -> tuple[str, str]:
    if score is None:
        return ("unknown", "Нет данных")
    for threshold, code, label in SCORE_BANDS:
        if score >= threshold:
            return (code, label)
    return ("weak", "Слабо / спекулятивно")
