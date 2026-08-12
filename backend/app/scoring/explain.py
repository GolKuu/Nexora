"""Deterministic score explanations.

This module is the *source of truth* for "Почему такая оценка?". It is written
in plain Python from the same components the score was built from, so the
explanation can never contradict the number. The LLM layer may later rephrase
this text, but it never invents the content.
"""

from __future__ import annotations

from app.scoring.results import ScoreResult

_VERDICTS = [
    (85.0, "отличная", "Одна из самых привлекательных бумаг в своей группе."),
    (70.0, "хорошая", "Заметно лучше среднего по своей группе."),
    (55.0, "средняя", "Обычный вариант: без явных преимуществ и без явных проблем."),
    (40.0, "ниже средней", "Есть слабые места, которые стоит изучить."),
    (0.0, "низкая", "Много слабых мест: подходит только при осознанном риске."),
]


def verdict(value: float | None) -> tuple[str, str]:
    if value is None:
        return "нет данных", "Недостаточно данных для оценки."
    for threshold, label, text in _VERDICTS:
        if value >= threshold:
            return label, text
    return "низкая", "Много слабых мест."


def explain_score(score: ScoreResult, *, limit: int = 3) -> dict:
    """Break a score into what helped it, what hurt it and what is missing."""
    label, summary = verdict(score.value)

    available = [c for c in score.components if c.value is not None]
    missing = [c for c in score.components if c.value is None and c.weight > 0]

    strengths = sorted(available, key=lambda c: -(c.value or 0))
    weaknesses = sorted(available, key=lambda c: (c.value or 0))

    def render(component) -> dict:
        return {
            "code": component.code,
            "label": component.label,
            "value": round(component.value, 1) if component.value is not None else None,
            "weight": round(component.weight, 4),
            "contribution": round(component.contribution, 2)
            if component.contribution is not None
            else None,
            "raw_value": component.raw_value,
            "raw_unit": component.raw_unit,
            "explanation": component.explanation,
        }

    return {
        "kind": score.kind,
        "value": score.value,
        "verdict": label,
        "summary": summary,
        "confidence": score.confidence,
        "version": score.version,
        "notes": score.notes,
        "strengths": [render(c) for c in strengths[:limit] if (c.value or 0) >= 55],
        "weaknesses": [render(c) for c in weaknesses[:limit] if (c.value or 100) < 55],
        "missing_data": [
            {"code": c.code, "label": c.label, "weight": round(c.weight, 4)}
            for c in missing
        ],
        "components": [render(c) for c in score.components],
    }


def explain_bundle(scores: dict[str, ScoreResult]) -> dict:
    """Explanation payload for the whole bond card."""
    investment = scores.get("investment")
    return {
        "investment": explain_score(investment) if investment else None,
        "by_kind": {kind: explain_score(score) for kind, score in scores.items()},
    }
