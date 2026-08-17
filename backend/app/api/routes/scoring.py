"""Strict scoring API.

Every response explains itself: component breakdown with raw values, weights,
reasons and sources; the red flags that cost points; the hard caps that were
triggered and which one actually bound; and the data limitations behind the
confidence number.

The scoring itself is done in Python. An LLM may rephrase these payloads for a
reader, but nothing in this path lets a model compute or adjust a score.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.db.session import get_session
from app.repositories.strict_scores import StrictScoreRepository
from app.scoring.strict.banks import BANK_WEIGHTS, BankScoringEngine
from app.scoring.strict.bonds import BOND_WEIGHTS, BondScoringEngine
from app.scoring.strict.caps import BANK_CAPS, BOND_CAPS, STOCK_CAPS
from app.scoring.strict.explain import explain
from app.scoring.strict.fingerprint import facts_fingerprint
from app.scoring.strict.parse import (
    FactsError,
    bond_facts_from_dict,
    stock_facts_from_dict,
)
from app.scoring.strict.redflags import MAX_TOTAL_PENALTY
from app.scoring.strict.results import StrictScore
from app.scoring.strict.scale import MISSING_PRIOR
from app.scoring.strict.stocks import STOCK_WEIGHTS, StockScoringEngine
from app.scoring.strict.versions import (
    BANK_MODEL,
    BOND_MODEL,
    SCORE_BANDS,
    STOCK_MODEL,
)

router = APIRouter()


def _as_of(value: str | None) -> datetime | None:
    """Parse the point-in-time valuation date.

    Passing a date here is a promise the engine keeps: nothing published after
    it can reach the score.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"Некорректная дата as_of: {value!r}.") from exc
    from datetime import timezone

    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _respond(
    score: StrictScore,
    *,
    ignored_fields: list[str],
    session: Session | None = None,
    fingerprint: str | None = None,
    persist: bool = False,
) -> dict:
    payload = explain(score)
    payload["score"] = score.to_dict()
    payload["ignored_fields"] = ignored_fields
    if ignored_fields:
        payload["data_limitations"] = [
            *payload["data_limitations"],
            "Неизвестные поля во входных данных проигнорированы: "
            + ", ".join(sorted(ignored_fields)) + ".",
        ]
    if persist and session is not None and fingerprint is not None:
        snapshot = StrictScoreRepository(session).save(score, fingerprint=fingerprint)
        session.commit()
        payload["snapshot_id"] = snapshot.id
    payload["facts_fingerprint"] = fingerprint
    return payload


@router.get("/scoring/model")
def scoring_model() -> dict:
    """The formulas themselves: versions, weights, caps and score bands.

    The frontend renders whatever this endpoint reports and never carries its
    own copy of the weights - otherwise the score shown and the score explained
    drift apart.
    """
    def _caps(rules) -> list[dict]:
        return [{"code": r.code, "ceiling": r.ceiling, "reason": r.reason} for r in rules]

    return {
        "versions": {
            "bond": BOND_MODEL.to_dict(),
            "stock": STOCK_MODEL.to_dict(),
            "bank": BANK_MODEL.to_dict(),
        },
        "weights": {"bond": BOND_WEIGHTS, "stock": STOCK_WEIGHTS, "bank": BANK_WEIGHTS},
        "caps": {"bond": _caps(BOND_CAPS), "stock": _caps(STOCK_CAPS), "bank": _caps(BANK_CAPS)},
        "bands": [
            {"min_score": threshold, "code": code, "label": label}
            for threshold, code, label in SCORE_BANDS
        ],
        "rules": {
            "max_red_flag_penalty": MAX_TOTAL_PENALTY,
            "missing_data_prior": MISSING_PRIOR,
            "notes": [
                "Пропущенные данные не считаются нулем и не могут улучшить оценку.",
                "Красные флаги уменьшают итоговый балл, а не только подсвечиваются.",
                "Высокая доходность не повышает оценку, если кредитное качество низкое.",
                "Исторические оценки используют только данные, доступные на выбранную дату.",
            ],
        },
    }


@router.post("/scoring/bond")
def score_bond(
    payload: dict[str, Any] = Body(...),
    as_of: str | None = Query(None, description="Оценка на дату (point-in-time)"),
    persist: bool = Query(False),
    session: Session = Depends(get_session),
) -> dict:
    try:
        facts, ignored = bond_facts_from_dict(payload)
    except FactsError as exc:
        raise ValidationError(str(exc)) from exc
    moment = _as_of(as_of)
    score = BondScoringEngine().score(facts, as_of=moment)
    return _respond(
        score,
        ignored_fields=ignored,
        session=session,
        fingerprint=facts_fingerprint(facts),
        persist=persist,
    )


@router.post("/scoring/stock")
def score_stock(
    payload: dict[str, Any] = Body(...),
    as_of: str | None = Query(None, description="Оценка на дату (point-in-time)"),
    persist: bool = Query(False),
    session: Session = Depends(get_session),
) -> dict:
    """Scores an ordinary company; ``is_bank: true`` routes to the bank model."""
    try:
        facts, ignored = stock_facts_from_dict(payload)
    except FactsError as exc:
        raise ValidationError(str(exc)) from exc
    moment = _as_of(as_of)
    score = StockScoringEngine().score(facts, as_of=moment)
    return _respond(
        score,
        ignored_fields=ignored,
        session=session,
        fingerprint=facts_fingerprint(facts),
        persist=persist,
    )


@router.post("/scoring/bank")
def score_bank(
    payload: dict[str, Any] = Body(...),
    as_of: str | None = Query(None),
    persist: bool = Query(False),
    session: Session = Depends(get_session),
) -> dict:
    try:
        facts, ignored = stock_facts_from_dict(payload)
    except FactsError as exc:
        raise ValidationError(str(exc)) from exc
    facts.is_bank = True
    moment = _as_of(as_of)
    score = BankScoringEngine().score(facts, as_of=moment)
    return _respond(
        score,
        ignored_fields=ignored,
        session=session,
        fingerprint=facts_fingerprint(facts),
        persist=persist,
    )


@router.get("/scoring/history/{ticker}")
def scoring_history(
    ticker: str,
    kind: str | None = Query(None, pattern="^(bond|stock|bank)$"),
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    """Every score ever written for this ticker, newest first.

    Rows are immutable. A score computed under an older model version keeps its
    old number and its old version id - the history is an audit trail, not a
    cache to be refreshed.
    """
    rows = StrictScoreRepository(session).history(ticker.upper(), kind=kind, limit=limit)
    return {
        "ticker": ticker.upper(),
        "count": len(rows),
        "snapshots": [
            {
                "id": row.id,
                "kind": row.kind,
                "model_version": row.model_version,
                "versions": row.versions,
                "as_of": row.as_of.isoformat() if row.as_of else None,
                "calculated_at": row.calculated_at.isoformat(),
                "final_score": row.final_score,
                "base_score": row.base_score,
                "data_quality": row.data_quality,
                "confidence": row.confidence,
                "band": row.band,
                "facts_fingerprint": row.facts_fingerprint,
            }
            for row in rows
        ],
    }


@router.get("/scoring/snapshot/{snapshot_id}")
def scoring_snapshot(snapshot_id: int, session: Session = Depends(get_session)) -> dict:
    """The full stored breakdown of one historical score."""
    from app.models.strict_scores import StrictScoreSnapshot

    row = session.get(StrictScoreSnapshot, snapshot_id)
    if row is None:
        raise ValidationError("Снимок оценки не найден.")
    return {
        "id": row.id,
        "kind": row.kind,
        "ticker": row.ticker,
        "model_version": row.model_version,
        "as_of": row.as_of.isoformat() if row.as_of else None,
        "calculated_at": row.calculated_at.isoformat(),
        "facts_fingerprint": row.facts_fingerprint,
        "breakdown": row.breakdown,
    }
