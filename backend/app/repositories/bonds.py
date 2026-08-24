from __future__ import annotations

from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.bond import Bond, BondCashFlow, PeerGroup


class BondRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, bond_id: int) -> Bond | None:
        return self.session.get(Bond, bond_id)

    def get_by_identifier(self, identifier: str) -> Bond | None:
        """Accepts a numeric id, a ticker or an ISIN."""
        key = identifier.strip()
        if key.isdigit():
            bond = self.session.get(Bond, int(key))
            if bond:
                return bond
        return self.session.execute(
            select(Bond)
            .options(joinedload(Bond.issuer))
            .where(
                or_(
                    func.upper(Bond.ticker) == key.upper(),
                    func.upper(Bond.isin) == key.upper(),
                )
            )
            .limit(1)
        ).scalar_one_or_none()

    def get_by_ticker(self, ticker: str) -> Bond | None:
        return self.session.execute(
            select(Bond).where(func.upper(Bond.ticker) == ticker.upper())
        ).scalar_one_or_none()

    def list(
        self,
        *,
        active_only: bool = True,
        bond_type: str | None = None,
        currency: str | None = None,
        issuer_id: int | None = None,
        max_years: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Bond]:
        stmt = select(Bond).options(joinedload(Bond.issuer))
        if active_only:
            stmt = stmt.where(
                Bond.is_active.is_(True),
                or_(Bond.maturity_date.is_(None), Bond.maturity_date >= date.today()),
            )
        if bond_type:
            stmt = stmt.where(Bond.bond_type == bond_type)
        if currency:
            stmt = stmt.where(Bond.currency == currency)
        if issuer_id:
            stmt = stmt.where(Bond.issuer_id == issuer_id)
        if max_years is not None:
            cutoff = date.today().replace(year=date.today().year + int(max_years))
            stmt = stmt.where(Bond.maturity_date <= cutoff)
        stmt = stmt.order_by(Bond.ticker).limit(limit).offset(offset)
        return list(self.session.execute(stmt).unique().scalars())

    def count(self, *, active_only: bool = True) -> int:
        stmt = select(func.count(Bond.id))
        if active_only:
            stmt = stmt.where(
                Bond.is_active.is_(True),
                or_(Bond.maturity_date.is_(None), Bond.maturity_date >= date.today()),
            )
        return int(self.session.execute(stmt).scalar_one())

    def search(self, query: str, limit: int = 20) -> list[Bond]:
        """Search by ticker, ISIN or issuer name.

        An exact ticker or ISIN match is ranked above every substring hit
        (§37): someone who types ``BRKZb14`` wants that bond first, not
        ``BRKZb14`` buried under ``BRKZb140``.
        """
        cleaned = query.strip()
        exact = cleaned.lower()
        needle = f"%{exact}%"
        stmt = (
            select(Bond)
            .options(joinedload(Bond.issuer))
            .where(
                or_(
                    func.lower(Bond.ticker).like(needle),
                    func.lower(Bond.name).like(needle),
                    func.lower(Bond.isin).like(needle),
                )
            )
            .order_by(Bond.ticker)
            .limit(max(limit, 50))
        )
        rows = list(self.session.execute(stmt).unique().scalars())

        def rank(bond: Bond) -> tuple[int, str]:
            ticker = (bond.ticker or "").lower()
            isin = (bond.isin or "").lower()
            if ticker == exact or isin == exact:
                return (0, ticker)
            if ticker.startswith(exact) or isin.startswith(exact):
                return (1, ticker)
            return (2, ticker)

        rows.sort(key=rank)
        return rows[:limit]

    def upsert(self, ticker: str, values: dict) -> Bond:
        bond = self.get_by_ticker(ticker)
        if bond is None:
            bond = Bond(ticker=ticker, **values)
            self.session.add(bond)
        else:
            for key, value in values.items():
                # Never overwrite a known value with None from a poorer source.
                if value is not None:
                    setattr(bond, key, value)
        self.session.flush()
        return bond


class CashFlowRepository:
    def __init__(self, session: Session):
        self.session = session

    def for_bond(self, bond_id: int) -> list[BondCashFlow]:
        return list(
            self.session.execute(
                select(BondCashFlow)
                .where(BondCashFlow.bond_id == bond_id)
                .order_by(BondCashFlow.payment_date)
            ).scalars()
        )

    def replace(self, bond_id: int, rows: list[dict]) -> None:
        for existing in self.for_bond(bond_id):
            self.session.delete(existing)
        self.session.flush()
        for row in rows:
            self.session.add(BondCashFlow(bond_id=bond_id, **row))
        self.session.flush()


class PeerGroupRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_or_create(self, code: str, **values) -> PeerGroup:
        group = self.session.execute(
            select(PeerGroup).where(PeerGroup.code == code)
        ).scalar_one_or_none()
        if group is None:
            group = PeerGroup(code=code, **values)
            self.session.add(group)
            self.session.flush()
        return group

    def members(self, group_id: int, exclude_bond_id: int | None = None) -> list[Bond]:
        stmt = select(Bond).where(
            Bond.peer_group_id == group_id,
            Bond.is_active.is_(True),
            or_(Bond.maturity_date.is_(None), Bond.maturity_date >= date.today()),
        )
        if exclude_bond_id:
            stmt = stmt.where(Bond.id != exclude_bond_id)
        return list(self.session.execute(stmt).scalars())
