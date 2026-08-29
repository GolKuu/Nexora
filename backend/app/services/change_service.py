"""Read models for bond, portfolio and daily change feeds."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.incremental import DataChangeSet, DataCurrentState
from app.models.portfolio import PortfolioPosition


class ChangeService:
    def __init__(self, session: Session):
        self.session = session

    def for_entity(
        self, entity_id: str, *, entity_type: str | None = None,
        since: datetime | None = None,
        section: str | None = None, importance: int | None = None, limit: int = 100,
    ) -> list[DataChangeSet]:
        query = select(DataChangeSet).where(DataChangeSet.entity_id == entity_id)
        if entity_type:
            query = query.where(DataChangeSet.entity_type == entity_type)
        if since:
            query = query.where(DataChangeSet.detected_at >= since)
        if section:
            query = query.where(DataChangeSet.section == section)
        if importance is not None:
            query = query.where(DataChangeSet.importance >= importance)
        return list(self.session.execute(
            query.order_by(DataChangeSet.detected_at.desc()).limit(limit)
        ).scalars())

    def summary(
        self, entity_id: str, *, entity_type: str | None = None,
        since: datetime | None = None,
    ) -> dict:
        changes = self.for_entity(
            entity_id, entity_type=entity_type, since=since, limit=5000
        )
        sections = {row.section for row in changes}
        fields = {row.field.rsplit(".", 1)[-1] for row in changes}
        return {
            "changed": bool(changes),
            "since": since.isoformat() if since else None,
            "material_changes": sum(1 for row in changes if row.material),
            "summary": {
                "price_changed": bool(fields & {"bid", "ask", "last", "clean_price"}),
                "yield_changed": "ytm" in fields,
                "credit_changed": bool(fields & {"credit_score", "rating"}),
                "new_documents": sum(1 for row in changes if row.section == "documents" and row.change_type == "created"),
                "sections": sorted(sections),
            },
        }

    def portfolio(self, portfolio_id: int, *, since: datetime | None = None, limit: int = 200) -> list[DataChangeSet]:
        positions = list(self.session.execute(select(PortfolioPosition).where(
            PortfolioPosition.portfolio_id == portfolio_id,
            PortfolioPosition.status == "EXECUTED",
        )).scalars())
        bond_ids = [str(row.bond_id) for row in positions if row.bond_id is not None]
        stock_ids = [str(row.stock_id) for row in positions if row.stock_id is not None]
        filters = []
        if bond_ids:
            filters.append(and_(DataChangeSet.entity_type == "bond", DataChangeSet.entity_id.in_(bond_ids)))
        if stock_ids:
            filters.append(and_(DataChangeSet.entity_type == "stock", DataChangeSet.entity_id.in_(stock_ids)))
        if not filters:
            return []
        query = select(DataChangeSet).where(or_(*filters))
        if since:
            query = query.where(DataChangeSet.detected_at >= since)
        return list(self.session.execute(query.order_by(DataChangeSet.detected_at.desc()).limit(limit)).scalars())

    def freshness(self, entity_id: str, *, entity_type: str | None = None) -> dict:
        query = select(DataCurrentState).where(DataCurrentState.entity_id == entity_id)
        if entity_type:
            query = query.where(DataCurrentState.entity_type == entity_type)
        states = list(self.session.execute(query).scalars())
        return {
            "last_checked_at": max((row.last_checked_at for row in states), default=None),
            "last_changed_at": max((row.last_changed_at for row in states), default=None),
            "source_timestamp": max((row.source_timestamp for row in states if row.source_timestamp), default=None),
        }


def serialize_change(row: DataChangeSet) -> dict:
    return {
        "id": row.id, "detected_at": row.detected_at.isoformat(), "ticker": row.ticker,
        "isin": row.isin, "section": row.section, "field": row.field,
        "old_value": row.old_value, "new_value": row.new_value,
        "change_type": row.change_type, "importance": row.importance,
        "material": row.material, "source_url": row.source_url,
        "source_timestamp": row.source_timestamp.isoformat() if row.source_timestamp else None,
        "parser_version": row.parser_version,
    }


__all__ = ["ChangeService", "serialize_change"]
