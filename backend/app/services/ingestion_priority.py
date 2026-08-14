"""Priority ordering for incremental source checks."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.portfolio import Alert, PortfolioPosition, Watchlist


def prioritized_tickers(session: Session) -> list[str]:
    """Portfolio > watchlist > active alerts > all remaining instruments."""
    priority: dict[int, int] = {}
    for bond_id in session.scalars(select(PortfolioPosition.bond_id).distinct()):
        priority[bond_id] = max(priority.get(bond_id, 0), 100)
    for bond_id in session.scalars(select(Watchlist.bond_id).distinct()):
        priority[bond_id] = max(priority.get(bond_id, 0), 90)
    for bond_id in session.scalars(select(Alert.bond_id).where(Alert.is_active.is_(True)).distinct()):
        priority[bond_id] = max(priority.get(bond_id, 0), 80)
    bonds = session.scalars(select(Bond).where(Bond.is_active.is_(True))).all()
    return [bond.ticker for bond in sorted(bonds, key=lambda row: (-priority.get(row.id, 10), row.ticker))]


__all__ = ["prioritized_tickers"]
