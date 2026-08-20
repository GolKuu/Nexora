"""Append-only persistence for strict score snapshots."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.strict_scores import StrictScoreSnapshot
from app.scoring.strict.results import StrictScore


class StrictScoreRepository:
    """Writes are inserts. There is deliberately no update and no delete.

    Re-scoring an instrument after new data arrives adds a row; the previous
    score stays exactly as it was published. A re-run with identical inputs and
    an identical model version finds its own earlier row and returns it instead
    of duplicating it.
    """

    def __init__(self, session: Session):
        self.session = session

    def find(
        self,
        *,
        ticker: str,
        kind: str,
        model_version: str,
        as_of: datetime | None,
        fingerprint: str,
    ) -> StrictScoreSnapshot | None:
        stmt = select(StrictScoreSnapshot).where(
            StrictScoreSnapshot.ticker == ticker,
            StrictScoreSnapshot.kind == kind,
            StrictScoreSnapshot.model_version == model_version,
            StrictScoreSnapshot.facts_fingerprint == fingerprint,
        )
        stmt = (
            stmt.where(StrictScoreSnapshot.as_of.is_(None))
            if as_of is None
            else stmt.where(StrictScoreSnapshot.as_of == as_of)
        )
        return self.session.execute(stmt.limit(1)).scalar_one_or_none()

    def save(
        self,
        score: StrictScore,
        *,
        fingerprint: str,
        bond_id: int | None = None,
        stock_id: int | None = None,
    ) -> StrictScoreSnapshot:
        ticker = score.ticker or "UNKNOWN"
        existing = self.find(
            ticker=ticker,
            kind=score.kind,
            model_version=score.version.model,
            as_of=score.as_of,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return existing

        band, _ = score.rating_band
        snapshot = StrictScoreSnapshot(
            kind=score.kind,
            ticker=ticker,
            bond_id=bond_id,
            stock_id=stock_id,
            model_version=score.version.model,
            versions=score.version.to_dict(),
            as_of=score.as_of,
            calculated_at=score.calculated_at,
            facts_fingerprint=fingerprint,
            final_score=score.final_score,
            base_score=score.base_score,
            penalised_score=score.penalised_score,
            data_quality=score.data_quality,
            confidence=score.confidence.value,
            band=band,
            breakdown=score.to_dict(),
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def history(
        self, ticker: str, *, kind: str | None = None, limit: int = 100
    ) -> list[StrictScoreSnapshot]:
        """Newest first, matched case-insensitively.

        KASE tickers are not all upper case - ``DBNKb1`` is a real one - so a
        history lookup that upper-cased its argument silently returned nothing
        for them. Writes still store the ticker exactly as it was published.
        """
        stmt = (
            select(StrictScoreSnapshot)
            .where(func.upper(StrictScoreSnapshot.ticker) == ticker.upper())
            .order_by(StrictScoreSnapshot.calculated_at.desc())
            .limit(limit)
        )
        if kind:
            stmt = stmt.where(StrictScoreSnapshot.kind == kind)
        return list(self.session.execute(stmt).scalars())

    def latest(self, ticker: str, *, kind: str | None = None) -> StrictScoreSnapshot | None:
        rows = self.history(ticker, kind=kind, limit=1)
        return rows[0] if rows else None
