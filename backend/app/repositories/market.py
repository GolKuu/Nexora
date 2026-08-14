from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import BondQuote, BondQuoteCurrent, BondTrade


class QuoteRepository:
    def __init__(self, session: Session):
        self.session = session

    def latest(self, bond_id: int) -> BondQuote | None:
        return self.session.execute(
            select(BondQuote)
            .where(BondQuote.bond_id == bond_id)
            .order_by(BondQuote.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()

    def latest_for_many(self, bond_ids: list[int]) -> dict[int, BondQuote]:
        if not bond_ids:
            return {}
        newest = (
            select(BondQuote.bond_id, func.max(BondQuote.timestamp).label("ts"))
            .where(BondQuote.bond_id.in_(bond_ids))
            .group_by(BondQuote.bond_id)
            .subquery()
        )
        rows = self.session.execute(
            select(BondQuote).join(
                newest,
                (BondQuote.bond_id == newest.c.bond_id)
                & (BondQuote.timestamp == newest.c.ts),
            )
        ).scalars()
        return {q.bond_id: q for q in rows}

    def history(self, bond_id: int, days: int = 180) -> list[BondQuote]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return list(
            self.session.execute(
                select(BondQuote)
                .where(BondQuote.bond_id == bond_id, BondQuote.timestamp >= cutoff)
                .order_by(BondQuote.timestamp)
            ).scalars()
        )

    def add(self, quote: BondQuote) -> BondQuote:
        self.session.add(quote)
        self.session.flush()
        return quote

    def current(self, bond_id: int) -> BondQuoteCurrent | None:
        return self.session.execute(
            select(BondQuoteCurrent).where(BondQuoteCurrent.bond_id == bond_id)
        ).scalar_one_or_none()

    def upsert_current(self, bond_id: int, values: dict) -> BondQuoteCurrent:
        row = self.current(bond_id)
        if row is None:
            row = BondQuoteCurrent(bond_id=bond_id, **values)
            self.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        self.session.flush()
        return row


class TradeRepository:
    def __init__(self, session: Session):
        self.session = session

    def recent(self, bond_id: int, days: int = 30) -> list[BondTrade]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return list(
            self.session.execute(
                select(BondTrade)
                .where(BondTrade.bond_id == bond_id, BondTrade.timestamp >= cutoff)
                .order_by(BondTrade.timestamp.desc())
            ).scalars()
        )

    def liquidity_stats(self, bond_id: int, days: int = 30) -> dict[str, float | None]:
        """Turnover and activity over the window. No trades means None, not 0.

        A bond that genuinely did not trade is different from a bond we have no
        trade data for; the caller distinguishes them by checking ``has_data``.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        row = self.session.execute(
            select(
                func.count(BondTrade.id),
                func.sum(BondTrade.amount),
                func.count(func.distinct(func.date(BondTrade.timestamp))),
            ).where(BondTrade.bond_id == bond_id, BondTrade.timestamp >= cutoff)
        ).one()
        count, total_amount, trading_days = row
        if not count:
            return {
                "has_data": False,
                "avg_daily_turnover": None,
                "trading_days": None,
                "trade_count": None,
            }
        return {
            "has_data": True,
            "avg_daily_turnover": (float(total_amount) / days) if total_amount else None,
            "trading_days": float(trading_days or 0),
            "trade_count": float(count),
        }

    def add(self, trade: BondTrade) -> BondTrade:
        self.session.add(trade)
        self.session.flush()
        return trade

    def add_if_new(self, trade: BondTrade) -> tuple[BondTrade, bool]:
        if trade.fingerprint:
            existing = self.session.execute(
                select(BondTrade).where(BondTrade.fingerprint == trade.fingerprint)
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False
        self.session.add(trade)
        self.session.flush()
        return trade, True
