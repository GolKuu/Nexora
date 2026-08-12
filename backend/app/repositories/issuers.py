from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.financials import CreditRating, FinancialStatement, IssuerMetric
from app.models.issuer import Issuer


class IssuerRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, issuer_id: int) -> Issuer | None:
        return self.session.get(Issuer, issuer_id)

    def get_by_code(self, code: str) -> Issuer | None:
        return self.session.execute(
            select(Issuer).where(func.upper(Issuer.code) == code.upper())
        ).scalar_one_or_none()

    def upsert(self, code: str, values: dict) -> Issuer:
        issuer = self.get_by_code(code)
        if issuer is None:
            issuer = Issuer(code=code.upper(), **values)
            self.session.add(issuer)
        else:
            for key, value in values.items():
                if value is not None:
                    setattr(issuer, key, value)
        self.session.flush()
        return issuer

    def statements(self, issuer_id: int, limit: int = 5) -> list[FinancialStatement]:
        return list(
            self.session.execute(
                select(FinancialStatement)
                .where(FinancialStatement.issuer_id == issuer_id)
                .order_by(FinancialStatement.period_end.desc())
                .limit(limit)
            ).scalars()
        )

    def latest_metric(self, issuer_id: int) -> IssuerMetric | None:
        return self.session.execute(
            select(IssuerMetric)
            .where(IssuerMetric.issuer_id == issuer_id)
            .order_by(IssuerMetric.period_end.desc())
            .limit(1)
        ).scalar_one_or_none()

    def metrics(self, issuer_id: int, limit: int = 5) -> list[IssuerMetric]:
        return list(
            self.session.execute(
                select(IssuerMetric)
                .where(IssuerMetric.issuer_id == issuer_id)
                .order_by(IssuerMetric.period_end.desc())
                .limit(limit)
            ).scalars()
        )

    def current_rating(self, issuer_id: int) -> CreditRating | None:
        return self.session.execute(
            select(CreditRating)
            .where(CreditRating.issuer_id == issuer_id, CreditRating.is_current.is_(True))
            .order_by(CreditRating.numeric_grade.asc().nulls_last())
            .limit(1)
        ).scalar_one_or_none()

    def save_metric(self, issuer_id: int, period_end, values: dict) -> IssuerMetric:
        existing = self.session.execute(
            select(IssuerMetric).where(
                IssuerMetric.issuer_id == issuer_id,
                IssuerMetric.period_end == period_end,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = IssuerMetric(issuer_id=issuer_id, period_end=period_end)
            self.session.add(existing)
        for key, value in values.items():
            setattr(existing, key, value)
        self.session.flush()
        return existing
