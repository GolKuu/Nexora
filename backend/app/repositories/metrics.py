from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.metrics import BondMetric


class MetricRepository:
    def __init__(self, session: Session):
        self.session = session

    def latest(self, bond_id: int) -> BondMetric | None:
        return self.session.execute(
            select(BondMetric)
            .where(BondMetric.bond_id == bond_id)
            .order_by(BondMetric.as_of.desc())
            .limit(1)
        ).scalar_one_or_none()

    def latest_for_many(self, bond_ids: list[int]) -> dict[int, BondMetric]:
        if not bond_ids:
            return {}
        newest = (
            select(BondMetric.bond_id, func.max(BondMetric.as_of).label("ts"))
            .where(BondMetric.bond_id.in_(bond_ids))
            .group_by(BondMetric.bond_id)
            .subquery()
        )
        rows = self.session.execute(
            select(BondMetric).join(
                newest,
                (BondMetric.bond_id == newest.c.bond_id)
                & (BondMetric.as_of == newest.c.ts),
            )
        ).scalars()
        return {m.bond_id: m for m in rows}

    def add(self, metric: BondMetric) -> BondMetric:
        self.session.add(metric)
        self.session.flush()
        return metric
