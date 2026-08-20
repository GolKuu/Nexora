"""The audit trail of an instrument's Investment Score, and why it moved.

Scores are stored append-only (see :mod:`app.models.strict_scores`), so the
history here is read, never recomputed: an old row keeps the number it was
published with, under the model version it was published under. This service
adds the one thing the stored rows do not carry on their own - what changed
between two consecutive rows.

A transition is explained strictly by comparing the two stored breakdowns. No
formula is re-run and nothing is inferred, so the explanation of a score change
cannot drift away from the scores it explains.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.strict_scores import StrictScoreSnapshot
from app.repositories.strict_scores import StrictScoreRepository

#: Score movements smaller than this are rounding, not news. The stored
#: breakdown already rounds components to two decimals.
MATERIAL_POINTS = 0.05


def _resolve(session: Session, identifier: str) -> dict:
    """Find a stock or a bond by ticker, ISIN or numeric id.

    Both services already accept all three forms, so an identifier a user can
    type into the search box is an identifier this endpoint accepts.
    """
    from app.services.bond_service import BondService
    from app.services.stock_service import StockService

    try:
        stock = StockService(session).require(identifier)
    except NotFoundError:
        pass
    else:
        return {
            "instrument_type": "stock",
            "instrument_id": stock.instrument_id,
            "ticker": stock.instrument.ticker,
            "isin": stock.instrument.isin,
            "name": stock.instrument.issuer.name if stock.instrument.issuer else None,
        }

    try:
        bond = BondService(session).require(identifier)
    except NotFoundError:
        raise NotFoundError(f"Инструмент не найден: {identifier}") from None
    return {
        "instrument_type": "bond",
        "instrument_id": getattr(bond, "instrument_id", None),
        "ticker": bond.ticker,
        "isin": bond.isin,
        "name": bond.name,
    }


def _components(breakdown: dict) -> dict[str, dict]:
    return {
        str(row.get("code")): row
        for row in (breakdown or {}).get("components", [])
        if row.get("code")
    }


def _codes(breakdown: dict, key: str) -> dict[str, dict]:
    return {
        str(row.get("code")): row
        for row in (breakdown or {}).get(key, [])
        if row.get("code")
    }


def _delta(new: float | None, old: float | None) -> float | None:
    if new is None or old is None:
        return None
    return round(float(new) - float(old), 2)


def explain_transition(
    newer: StrictScoreSnapshot, older: StrictScoreSnapshot
) -> dict:
    """What changed between two consecutive scores of the same instrument.

    Answers the four questions the score history has to answer when a user
    clicks a change: how far the score moved, which component moved it, which
    red flag appeared, and which hard cap started binding.
    """
    new_breakdown = newer.breakdown or {}
    old_breakdown = older.breakdown or {}

    components = []
    new_components, old_components = _components(new_breakdown), _components(old_breakdown)
    for code in sorted(set(new_components) | set(old_components)):
        after, before = new_components.get(code, {}), old_components.get(code, {})
        change = _delta(after.get("score"), before.get("score"))
        appeared = before.get("score") is None and after.get("score") is not None
        lost = before.get("score") is not None and after.get("score") is None
        if change is None and not (appeared or lost):
            continue
        if change is not None and abs(change) < MATERIAL_POINTS:
            continue
        components.append({
            "code": code,
            "label": after.get("label") or before.get("label"),
            "from": before.get("score"),
            "to": after.get("score"),
            "delta": change,
            # Data arriving is a real event even when the component's own score
            # is unchanged, and so is data going missing.
            "became_available": appeared,
            "became_unavailable": lost,
            "raw_from": before.get("raw_value"),
            "raw_to": after.get("raw_value"),
            "unit": after.get("unit") or before.get("unit"),
            "reason": after.get("reason"),
        })
    components.sort(key=lambda row: abs(row["delta"] or 0.0), reverse=True)

    new_flags, old_flags = _codes(new_breakdown, "red_flags"), _codes(old_breakdown, "red_flags")
    new_caps, old_caps = _codes(new_breakdown, "caps"), _codes(old_breakdown, "caps")

    def binding(rows: dict[str, dict]) -> set[str]:
        return {code for code, row in rows.items() if row.get("binding")}

    score_delta = _delta(newer.final_score, older.final_score)
    formula_changed = newer.model_version != older.model_version
    return {
        "from_snapshot_id": older.id,
        "to_snapshot_id": newer.id,
        "from": round(float(older.final_score), 1),
        "to": round(float(newer.final_score), 1),
        "delta": score_delta,
        "direction": (
            "unchanged" if not score_delta
            else ("up" if score_delta > 0 else "down")
        ),
        "confidence_delta": _delta(newer.confidence, older.confidence),
        "data_quality_delta": _delta(newer.data_quality, older.data_quality),
        "band_from": older.band,
        "band_to": newer.band,
        "components_changed": components,
        "red_flags_raised": [new_flags[code] for code in sorted(set(new_flags) - set(old_flags))],
        "red_flags_cleared": [old_flags[code] for code in sorted(set(old_flags) - set(new_flags))],
        "caps_applied": [new_caps[code] for code in sorted(binding(new_caps) - binding(old_caps))],
        "caps_lifted": [old_caps[code] for code in sorted(binding(old_caps) - binding(new_caps))],
        # A score can move because the facts moved or because the formula did.
        # Saying which is the difference between an audit trail and a mystery.
        "model_version_changed": formula_changed,
        "model_version_from": older.model_version if formula_changed else None,
        "model_version_to": newer.model_version if formula_changed else None,
    }


def _snapshot(row: StrictScoreSnapshot) -> dict:
    return {
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


class ScoreHistoryService:
    def __init__(self, session: Session):
        self.session = session

    def history(self, identifier: str, *, kind: str | None = None, limit: int = 50) -> dict:
        instrument = _resolve(self.session, identifier)
        rows = StrictScoreRepository(self.session).history(
            instrument["ticker"], kind=kind, limit=limit
        )

        # Scores of different kinds are different measurements of different
        # things. Diffing a bank score against a bond score would produce a
        # number that means nothing, so transitions stay within one kind.
        by_kind: dict[str, list[StrictScoreSnapshot]] = {}
        for row in rows:
            by_kind.setdefault(row.kind, []).append(row)

        transitions = [
            explain_transition(newer, older)
            for group in by_kind.values()
            for newer, older in zip(group, group[1:])
        ]
        transitions.sort(key=lambda row: row["to_snapshot_id"], reverse=True)

        return {
            **instrument,
            "kind": kind,
            "count": len(rows),
            "snapshots": [_snapshot(row) for row in rows],
            "transitions": transitions,
            "note": (
                "Оценки хранятся только на добавление. Прошлая оценка сохраняет "
                "своё число и свою версию модели и никогда не пересчитывается."
            ),
        }


__all__ = ["MATERIAL_POINTS", "ScoreHistoryService", "explain_transition"]
