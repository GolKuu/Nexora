"""The backfill queue: who gets crawled, in what order, and how to resume.

Priority follows the spec: instruments somebody actually holds come first, then
instruments somebody is watching, then the liquid names most people will open,
then the rest of the discovered universe. Every stock reaches the queue
eventually - the ordering decides who waits, not who is skipped.

Checkpoints make a long crawl resumable. A run that dies two thirds of the way
through a two-year window continues from ``last_processed_timestamp`` instead of
restarting, which matters both for finishing and for not re-requesting pages
from kase.kz that we already read.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.history import BackfillCheckpoint
from app.models.instrument import Instrument
from app.models.portfolio import PortfolioPosition, Watchlist
from app.models.stock import Stock
from app.services.backfill.window import BackfillWindow

JOB_MARKET_HISTORY = "market_history"

PRIORITY_PORTFOLIO = 100
PRIORITY_WATCHLIST = 200
PRIORITY_LIQUID = 300
PRIORITY_RECOMMENDED = 400
PRIORITY_UNIVERSE = 500

STATUS_QUEUED = "queued"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"


class BackfillQueue:
    def __init__(self, session: Session, *, job_type: str = JOB_MARKET_HISTORY):
        self.session = session
        self.job_type = job_type

    # -- enrolment --------------------------------------------------------

    def priority_for(self, instrument: Instrument, stock: Stock | None) -> int:
        """Lower number, earlier crawl."""
        if stock is None:
            return PRIORITY_UNIVERSE
        held = self.session.execute(
            select(func.count(PortfolioPosition.id)).where(
                PortfolioPosition.stock_id == stock.id
            )
        ).scalar_one()
        if held:
            return PRIORITY_PORTFOLIO
        watched = self.session.execute(
            select(func.count(Watchlist.id)).where(Watchlist.stock_id == stock.id)
        ).scalar_one()
        if watched:
            return PRIORITY_WATCHLIST
        # liquidity_class 1 is the most liquid tier on KASE.
        if stock.liquidity_class is not None and stock.liquidity_class <= 1:
            return PRIORITY_LIQUID
        if stock.liquidity_class is not None and stock.liquidity_class <= 2:
            return PRIORITY_RECOMMENDED
        return PRIORITY_UNIVERSE

    def enqueue(
        self, instrument: Instrument, window: BackfillWindow, *, stock: Stock | None = None
    ) -> BackfillCheckpoint:
        """Put an instrument in the queue, or refresh its priority in place.

        Newly discovered stocks land here automatically from the catalogue
        collector - adding a KASE listing never requires a code change.
        """
        checkpoint = self.get(instrument.id)
        priority = self.priority_for(instrument, stock)
        if checkpoint is None:
            checkpoint = BackfillCheckpoint(
                job_type=self.job_type,
                instrument_id=instrument.id,
                range_start=window.start,
                range_end=window.end,
                status=STATUS_QUEUED,
                priority=priority,
                attempts=0,
            )
            self.session.add(checkpoint)
        else:
            checkpoint.priority = priority
            # The window rolls forward with the clock; the far end always
            # extends to now so newly available days get picked up.
            checkpoint.range_end = window.end
            if checkpoint.status in (STATUS_COMPLETED, STATUS_PARTIAL):
                checkpoint.status = STATUS_QUEUED
        self.session.flush()
        return checkpoint

    def get(self, instrument_id: int) -> BackfillCheckpoint | None:
        return self.session.execute(
            select(BackfillCheckpoint).where(
                BackfillCheckpoint.job_type == self.job_type,
                BackfillCheckpoint.instrument_id == instrument_id,
            )
        ).scalar_one_or_none()

    # -- claiming work ----------------------------------------------------

    def next_batch(
        self, *, limit: int | None = None, now: datetime | None = None
    ) -> list[BackfillCheckpoint]:
        """The next instruments to crawl, in priority order."""
        now = now or datetime.now(timezone.utc)
        limit = limit or settings.BACKFILL_BATCH_SIZE
        rows = self.session.execute(
            select(BackfillCheckpoint)
            .where(
                BackfillCheckpoint.job_type == self.job_type,
                BackfillCheckpoint.status.in_((STATUS_QUEUED, STATUS_PARTIAL, STATUS_FAILED)),
                BackfillCheckpoint.attempts < settings.BACKFILL_MAX_RETRIES,
            )
            .order_by(
                BackfillCheckpoint.priority.asc(),
                BackfillCheckpoint.updated_at.asc(),
            )
            .limit(limit * 4)
        ).scalars()
        ready = [
            row for row in rows
            if row.next_attempt_at is None or row.next_attempt_at <= now
        ]
        return ready[:limit]

    def start(self, checkpoint: BackfillCheckpoint) -> BackfillCheckpoint:
        checkpoint.status = STATUS_PROCESSING
        checkpoint.attempts += 1
        self.session.flush()
        return checkpoint

    def advance(
        self,
        checkpoint: BackfillCheckpoint,
        *,
        last_timestamp: datetime | None = None,
        cursor: str | None = None,
    ) -> BackfillCheckpoint:
        """Record progress so a crash resumes here instead of at the start."""
        if last_timestamp is not None:
            current = checkpoint.last_processed_timestamp
            # SQLite hands back naive datetimes; compare in UTC either way.
            if current is not None and current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            if current is None or last_timestamp > current:
                checkpoint.last_processed_timestamp = last_timestamp
        if cursor is not None:
            checkpoint.last_processed_cursor = cursor
        self.session.flush()
        return checkpoint

    def finish(
        self,
        checkpoint: BackfillCheckpoint,
        *,
        status: str,
        error: str | None = None,
        retry_in: timedelta | None = None,
    ) -> BackfillCheckpoint:
        checkpoint.status = status
        checkpoint.last_error = error
        if status in (STATUS_COMPLETED, STATUS_PARTIAL):
            checkpoint.attempts = 0
            checkpoint.next_attempt_at = None
        elif retry_in is not None:
            checkpoint.next_attempt_at = datetime.now(timezone.utc) + retry_in
        self.session.flush()
        return checkpoint

    def backoff(self, attempts: int) -> timedelta:
        """Exponential, capped. Being blocked means waiting longer, not harder."""
        base = settings.BACKFILL_REQUEST_DELAY_MS / 1000.0
        return timedelta(seconds=min(base * (2 ** max(attempts, 1)) * 60, 3600.0))

    # -- reporting --------------------------------------------------------

    def counts(self) -> dict[str, int]:
        rows = self.session.execute(
            select(BackfillCheckpoint.status, func.count(BackfillCheckpoint.id))
            .where(BackfillCheckpoint.job_type == self.job_type)
            .group_by(BackfillCheckpoint.status)
        ).all()
        return {status: count for status, count in rows}


__all__ = [
    "BackfillQueue",
    "JOB_MARKET_HISTORY",
    "PRIORITY_LIQUID",
    "PRIORITY_PORTFOLIO",
    "PRIORITY_RECOMMENDED",
    "PRIORITY_UNIVERSE",
    "PRIORITY_WATCHLIST",
    "STATUS_BLOCKED",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PARTIAL",
    "STATUS_PROCESSING",
    "STATUS_QUEUED",
]
