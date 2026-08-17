"""Deterministic explanations.

The text here is generated from the same components that produced the number, in
plain Python. An LLM may rephrase this payload for a user, but it must never
compute, adjust or contradict a score - which is why every sentence below is
derived, not written.
"""

from __future__ import annotations

from app.scoring.strict.results import StrictScore
from app.scoring.strict.scale import ComponentScore

STRONG = 70.0
WEAK = 50.0


def _render(component: ComponentScore) -> dict:
    return {
        "code": component.code,
        "label": component.label,
        "score": None if component.score is None else round(component.score, 1),
        "weight": round(component.weight, 4),
        "contribution": component.contribution,
        "raw_value": component.raw_value,
        "unit": component.unit,
        "reason": component.reason,
        "source": component.source,
        "as_of": component.as_of.isoformat() if component.as_of else None,
    }


def _summary(score: StrictScore, strengths: list[ComponentScore]) -> str:
    band = score.rating_band[1]
    binding = score.binding_caps
    lead = (
        f"{strengths[0].label.lower()} — сильная сторона"
        if strengths
        else "явных сильных сторон нет"
    )
    if binding:
        cap = binding[0]
        return (
            f"{lead.capitalize()}, но {cap.reason.lower().rstrip('.')} — "
            f"итоговая оценка ограничена {score.final_score:.0f}/100."
        )
    critical = [f for f in score.red_flags if f.severity in ("critical", "high")]
    if critical:
        return (
            f"{lead.capitalize()}, однако есть серьезные предупреждения: "
            f"{critical[0].message.rstrip('.')} — итог {score.final_score:.0f}/100 ({band.lower()})."
        )
    return f"Итоговая оценка {score.final_score:.0f}/100 — {band.lower()}."


def explain(score: StrictScore, *, limit: int = 3) -> dict:
    """The payload the API returns alongside every score."""
    weighted = [c for c in score.components if c.weight > 0]
    available = [c for c in weighted if c.score is not None]

    strengths = [c for c in sorted(available, key=lambda c: -(c.score or 0)) if (c.score or 0) >= STRONG]
    weaknesses = [c for c in sorted(available, key=lambda c: (c.score or 0)) if (c.score or 0) < WEAK]
    missing = [c for c in weighted if c.score is None]

    limitations = list(score.confidence.limitations)
    if score.excluded_facts:
        limitations.append(
            "Не учтено как опубликованное позже даты оценки: " + "; ".join(score.excluded_facts) + "."
        )
    if missing:
        limitations.append(
            "Не измерено: " + ", ".join(c.label for c in missing)
            + " — вес этих блоков засчитан консервативно."
        )

    band_code, band_label = score.rating_band
    return {
        "ticker": score.ticker,
        "kind": score.kind,
        "final_score": round(score.final_score, 1),
        "base_score": round(score.base_score, 1),
        "band": band_code,
        "band_label": band_label,
        "version": score.version.to_dict(),
        "calculated_at": score.calculated_at.isoformat(),
        "as_of": score.as_of.isoformat() if score.as_of else None,
        "summary": _summary(score, strengths),
        "data_quality": round(score.data_quality, 1),
        "confidence": round(score.confidence.value, 1),
        "strengths": [_render(c) for c in strengths[:limit]],
        "weaknesses": [_render(c) for c in weaknesses[:limit]],
        "red_flags": [f.to_dict() for f in score.red_flags],
        "binding_caps": [c.to_dict() for c in score.binding_caps],
        "all_caps": [c.to_dict() for c in score.caps],
        "data_limitations": limitations,
        "components": [_render(c) for c in score.components],
        "breakdown": [c.to_dict() for c in score.components],
        "notes": score.notes,
    }
