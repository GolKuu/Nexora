"""Peer grouping and peer statistics for relative value."""

from __future__ import annotations

import statistics

from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.repositories.bonds import BondRepository, PeerGroupRepository
from app.repositories.metrics import MetricRepository


def maturity_bucket(years: float | None) -> str:
    if years is None:
        return "unknown"
    if years <= 1:
        return "0-1y"
    if years <= 3:
        return "1-3y"
    if years <= 5:
        return "3-5y"
    if years <= 10:
        return "5-10y"
    return "10y+"


def peer_code(bond: Bond, years: float | None) -> str:
    kind = bond.bond_type or "unknown"
    return f"{bond.currency}:{kind}:{maturity_bucket(years)}"


class PeerService:
    def __init__(self, session: Session):
        self.session = session
        self.bonds = BondRepository(session)
        self.groups = PeerGroupRepository(session)
        self.metrics = MetricRepository(session)

    def assign(self, bond: Bond, years_to_maturity: float | None) -> int | None:
        code = peer_code(bond, years_to_maturity)
        group = self.groups.get_or_create(
            code,
            name=code,
            currency=bond.currency,
            bond_type=bond.bond_type,
            maturity_bucket=maturity_bucket(years_to_maturity),
            description="Автоматическая группа сравнения: валюта, тип выпуска, срок.",
        )
        bond.peer_group_id = group.id
        self.session.flush()
        return group.id

    def stats(self, bond: Bond) -> dict:
        """Median yield, spread and duration among comparable bonds."""
        if bond.peer_group_id is None:
            return {"peer_count": 0}
        peers = self.groups.members(bond.peer_group_id, exclude_bond_id=bond.id)
        if not peers:
            return {"peer_count": 0}
        metrics = self.metrics.latest_for_many([p.id for p in peers])
        ytms = [m.ytm for m in metrics.values() if m.ytm is not None]
        spreads = [m.credit_spread for m in metrics.values() if m.credit_spread is not None]
        durations = [
            m.modified_duration for m in metrics.values() if m.modified_duration is not None
        ]
        return {
            "peer_count": len(ytms),
            "peer_ids": [p.id for p in peers],
            "peer_median_ytm": statistics.median(ytms) if ytms else None,
            "peer_median_spread": statistics.median(spreads) if spreads else None,
            "peer_median_duration": statistics.median(durations) if durations else None,
        }

    def peers_with_metrics(self, bond: Bond, limit: int = 8) -> list[dict]:
        if bond.peer_group_id is None:
            return []
        peers = self.groups.members(bond.peer_group_id, exclude_bond_id=bond.id)[:limit]
        metrics = self.metrics.latest_for_many([p.id for p in peers])
        rows = []
        for peer in peers:
            metric = metrics.get(peer.id)
            rows.append(
                {
                    "id": peer.id,
                    "ticker": peer.ticker,
                    "name": peer.name,
                    "ytm": metric.ytm if metric else None,
                    "real_ytm": metric.real_ytm if metric else None,
                    "modified_duration": metric.modified_duration if metric else None,
                    "years_to_maturity": metric.years_to_maturity if metric else None,
                }
            )
        return rows
