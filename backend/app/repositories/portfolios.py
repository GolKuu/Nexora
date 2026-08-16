from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.portfolio import Alert, Portfolio, PortfolioPosition, Watchlist


class PortfolioRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_for_owner(self, *, user_id: int | None, token: str | None) -> list[Portfolio]:
        stmt = select(Portfolio).options(selectinload(Portfolio.positions))
        if user_id is not None:
            stmt = stmt.where(Portfolio.user_id == user_id)
        elif token:
            stmt = stmt.where(Portfolio.anonymous_token == token)
        else:
            return []
        return list(self.session.execute(stmt.order_by(Portfolio.id)).unique().scalars())

    def get(self, portfolio_id: int) -> Portfolio | None:
        return self.session.execute(
            select(Portfolio)
            .options(
                selectinload(Portfolio.positions).selectinload(PortfolioPosition.bond),
                selectinload(Portfolio.positions).selectinload(PortfolioPosition.stock),
            )
            .where(Portfolio.id == portfolio_id)
        ).unique().scalar_one_or_none()

    def create(self, **values) -> Portfolio:
        portfolio = Portfolio(**values)
        self.session.add(portfolio)
        self.session.flush()
        return portfolio

    def add_position(self, portfolio_id: int, **values) -> PortfolioPosition:
        position = PortfolioPosition(portfolio_id=portfolio_id, **values)
        self.session.add(position)
        self.session.flush()
        return position

    def get_position(self, position_id: int) -> PortfolioPosition | None:
        return self.session.get(PortfolioPosition, position_id)

    def delete_position(self, position: PortfolioPosition) -> None:
        self.session.delete(position)
        self.session.flush()


class WatchlistRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_for_owner(self, *, user_id: int | None, token: str | None) -> list[Watchlist]:
        stmt = select(Watchlist).options(selectinload(Watchlist.bond), selectinload(Watchlist.stock))
        if user_id is not None:
            stmt = stmt.where(Watchlist.user_id == user_id)
        elif token:
            stmt = stmt.where(Watchlist.anonymous_token == token)
        else:
            return []
        return list(self.session.execute(stmt).unique().scalars())

    def find(self, *, user_id: int | None, token: str | None, bond_id: int | None = None, stock_id: int | None = None) -> Watchlist | None:
        if (bond_id is None) == (stock_id is None):
            return None
        stmt = select(Watchlist).where(Watchlist.bond_id == bond_id) if bond_id is not None else select(Watchlist).where(Watchlist.stock_id == stock_id)
        if user_id is not None:
            stmt = stmt.where(Watchlist.user_id == user_id)
        elif token:
            stmt = stmt.where(Watchlist.anonymous_token == token)
        else:
            return None
        return self.session.execute(stmt).scalar_one_or_none()

    def add(self, **values) -> Watchlist:
        entry = Watchlist(**values)
        self.session.add(entry)
        self.session.flush()
        return entry

    def remove(self, entry: Watchlist) -> None:
        self.session.delete(entry)
        self.session.flush()


class AlertRepository:
    def __init__(self, session: Session):
        self.session = session

    def list_for_owner(self, *, user_id: int | None, token: str | None) -> list[Alert]:
        stmt = select(Alert).options(selectinload(Alert.bond), selectinload(Alert.stock))
        if user_id is not None:
            stmt = stmt.where(Alert.user_id == user_id)
        elif token:
            stmt = stmt.where(Alert.anonymous_token == token)
        else:
            return []
        return list(self.session.execute(stmt.order_by(Alert.id)).scalars())

    def get_for_owner(self, alert_id: int, *, user_id: int | None, token: str | None) -> Alert | None:
        stmt = select(Alert).where(Alert.id == alert_id)
        if user_id is not None:
            stmt = stmt.where(Alert.user_id == user_id)
        elif token:
            stmt = stmt.where(Alert.anonymous_token == token)
        else:
            return None
        return self.session.execute(stmt).scalar_one_or_none()

    def add(self, **values) -> Alert:
        alert = Alert(**values)
        self.session.add(alert)
        self.session.flush()
        return alert

    def remove(self, alert: Alert) -> None:
        self.session.delete(alert)
        self.session.flush()
