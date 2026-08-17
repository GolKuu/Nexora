"""Shared assembly step for every strict scoring engine.

The pipeline is fixed and identical for bonds, stocks and banks:

    weighted base score -> red flag penalties -> hard caps -> final score

Keeping it in one place is what makes "red flags affect the score" and "caps
bind" true by construction rather than by three separate good intentions.
"""

from __future__ import annotations

from datetime import datetime

from app.scoring.strict.caps import CapRule, ScoreCapEngine
from app.scoring.strict.confidence import DataQualityResult
from app.scoring.strict.redflags import RedFlag, total_penalty
from app.scoring.strict.results import Confidence, StrictScore
from app.scoring.strict.scale import ComponentScore, aggregate, clamp
from app.scoring.strict.versions import ModelVersion

_caps = ScoreCapEngine()


def component_map(components: list[ComponentScore]) -> dict[str, float | None]:
    return {c.code: c.score for c in components}


def finalise(
    *,
    kind: str,
    ticker: str | None,
    model: ModelVersion,
    components: list[ComponentScore],
    flags: list[RedFlag],
    cap_rules: tuple[CapRule, ...],
    cap_state: dict,
    data_quality: DataQualityResult,
    confidence: Confidence,
    as_of: datetime | None,
    excluded_facts: list[str],
    notes: list[str],
    now: datetime,
) -> StrictScore:
    base = aggregate(components).value
    penalty = total_penalty(flags)
    penalised = clamp(base - penalty)

    triggered = _caps.evaluate(cap_rules, cap_state)
    final, resolved = _caps.apply(penalised, triggered)

    if penalty > 0:
        notes = [*notes, f"Штраф за красные флаги: -{penalty:.1f} балла."]
    binding = [c for c in resolved if c.binding]
    if binding:
        notes = [
            *notes,
            "Ограничение: " + "; ".join(f"{c.reason} (не выше {c.ceiling:.0f})" for c in binding),
        ]

    return StrictScore(
        kind=kind,
        ticker=ticker,
        version=model,
        calculated_at=now,
        as_of=as_of,
        base_score=base,
        penalised_score=penalised,
        final_score=clamp(final),
        data_quality=data_quality.value,
        confidence=confidence,
        components=components,
        red_flags=flags,
        caps=resolved,
        excluded_facts=excluded_facts,
        notes=notes,
    )
