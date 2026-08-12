from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.scores import BondScore, ScoreComponent
from app.scoring.results import ScoreResult


class ScoreRepository:
    def __init__(self, session: Session):
        self.session = session

    def latest(self, bond_id: int, kind: str, user_id: int | None = None) -> BondScore | None:
        stmt = (
            select(BondScore)
            .options(selectinload(BondScore.components))
            .where(BondScore.bond_id == bond_id, BondScore.kind == kind)
            .order_by(BondScore.calculated_at.desc())
            .limit(1)
        )
        stmt = stmt.where(BondScore.user_id == user_id) if user_id else stmt.where(
            BondScore.user_id.is_(None)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def latest_all(self, bond_id: int) -> dict[str, BondScore]:
        newest = (
            select(BondScore.kind, func.max(BondScore.calculated_at).label("ts"))
            .where(BondScore.bond_id == bond_id, BondScore.user_id.is_(None))
            .group_by(BondScore.kind)
            .subquery()
        )
        rows = self.session.execute(
            select(BondScore)
            .options(selectinload(BondScore.components))
            .join(
                newest,
                (BondScore.kind == newest.c.kind)
                & (BondScore.calculated_at == newest.c.ts),
            )
            .where(BondScore.bond_id == bond_id, BondScore.user_id.is_(None))
        ).scalars()
        return {s.kind: s for s in rows}

    def latest_investment_for_many(self, bond_ids: list[int]) -> dict[int, BondScore]:
        if not bond_ids:
            return {}
        newest = (
            select(BondScore.bond_id, func.max(BondScore.calculated_at).label("ts"))
            .where(
                BondScore.bond_id.in_(bond_ids),
                BondScore.kind == "investment",
                BondScore.user_id.is_(None),
            )
            .group_by(BondScore.bond_id)
            .subquery()
        )
        rows = self.session.execute(
            select(BondScore).join(
                newest,
                (BondScore.bond_id == newest.c.bond_id)
                & (BondScore.calculated_at == newest.c.ts),
            )
        ).scalars()
        return {s.bond_id: s for s in rows}

    def save(self, bond_id: int, result: ScoreResult, user_id: int | None = None) -> BondScore:
        score = BondScore(
            bond_id=bond_id,
            kind=result.kind,
            value=result.value,
            version=result.version,
            calculated_at=result.calculated_at,
            confidence=result.confidence,
            user_id=user_id,
            inputs=result.inputs or None,
            notes=result.notes,
        )
        self.session.add(score)
        self.session.flush()
        for component in result.components:
            self.session.add(
                ScoreComponent(
                    score_id=score.id,
                    code=component.code,
                    label=component.label,
                    value=component.value,
                    weight=component.weight,
                    contribution=component.contribution,
                    raw_value=component.raw_value,
                    raw_unit=component.raw_unit,
                    available=component.available,
                    explanation=component.explanation,
                )
            )
        self.session.flush()
        return score

    def save_all(self, bond_id: int, results: dict[str, ScoreResult]) -> None:
        for result in results.values():
            self.save(bond_id, result)
