"""The backfill orchestrator.

    queue -> collect -> validate -> store -> daily snapshots -> coverage

Each instrument is one unit of work with its own checkpoint, so a failure in the
middle of the universe costs one instrument's progress, not the whole run.
Concurrency is deliberately small and configurable; the default is a single
browser context with a pause between requests.

The collector is injectable, which is what lets the offline tests exercise the
whole pipeline - including idempotency, resume and anomaly handling - without a
browser or a network.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.history import BackfillCheckpoint
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock
from app.services.backfill.collector import KaseHistoryCollector, PageHistory
from app.services.backfill.coverage import CoverageService, FAILED, UNAVAILABLE
from app.services.backfill.queue import (
    BackfillQueue,
    STATUS_BLOCKED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
)
from app.services.backfill.records import CollectionResult
from app.services.backfill.store import HistoryStore
from app.services.backfill.validate import (
    validate_observations,
    validate_trades,
)
from app.services.backfill.window import BackfillWindow, backfill_window

logger = get_logger(__name__)


class HistorySource(Protocol):
    """What the runner needs from a collector. The browser one satisfies it."""

    async def collect(
        self,
        ticker: str,
        window: BackfillWindow,
        *,
        url: str | None = ...,
        since: datetime | None = ...,
    ) -> PageHistory: ...


class BackfillRunner:
    def __init__(
        self,
        session: Session,
        *,
        collector: HistorySource | None = None,
        window: BackfillWindow | None = None,
    ):
        self.session = session
        self.collector = collector or KaseHistoryCollector()
        self.window = window or backfill_window()
        self.queue = BackfillQueue(session)
        self.store = HistoryStore(session)
        self.coverage = CoverageService(session)

    # -- enrolment --------------------------------------------------------

    def enrol_all(self) -> dict:
        """Put every discovered stock in the queue.

        Called after catalogue discovery, so a stock that appears on KASE
        tomorrow is queued tomorrow without anybody touching the code.
        """
        rows = self.session.execute(
            select(Instrument, Stock)
            .join(Stock, Stock.instrument_id == Instrument.id)
            .where(Instrument.instrument_type == "stock")
        ).all()
        queued = 0
        for instrument, stock in rows:
            if not instrument.is_active:
                # Delisted names keep their history and their queue row, but
                # they are not crawled again on the fast cadence.
                continue
            self.queue.enqueue(instrument, self.window, stock=stock)
            queued += 1
        self.session.commit()
        return {"discovered": len(rows), "queued": queued, **self.window.to_dict()}

    def _issuer_code(self, instrument: Instrument) -> str | None:
        issuer = self.session.get(Issuer, instrument.issuer_id)
        return issuer.code if issuer else None

    # -- one instrument ---------------------------------------------------

    async def backfill_instrument(
        self, instrument: Instrument, *, checkpoint: BackfillCheckpoint | None = None
    ) -> dict:
        checkpoint = checkpoint or self.queue.enqueue(instrument, self.window)
        self.queue.start(checkpoint)
        self.session.commit()

        summary = {
            "ticker": instrument.ticker,
            "instrument_id": instrument.id,
            "observations": {"created": 0, "duplicates": 0, "received": 0},
            "trades": {"created": 0, "duplicates": 0, "received": 0},
            "dividends": {"created": 0, "duplicates": 0, "received": 0},
            "reports": {"created": 0, "duplicates": 0, "received": 0},
            "news": {"created": 0, "duplicates": 0, "received": 0},
            "anomalies": 0,
            "blocked": False,
        }

        try:
            page = await self.collector.collect(
                instrument.ticker,
                self.window,
                url=instrument.kase_url,
                since=checkpoint.last_processed_timestamp,
            )
        except Exception as exc:
            logger.warning("backfill failed for %s: %s", instrument.ticker, exc)
            self.queue.finish(
                checkpoint,
                status=STATUS_FAILED,
                error=str(exc)[:2000],
                retry_in=self.queue.backoff(checkpoint.attempts),
            )
            self.coverage.measure(instrument, self.window, status=FAILED)
            self.session.commit()
            return {**summary, "status": STATUS_FAILED, "error": str(exc)}

        result: CollectionResult = page.result
        if result.blocked:
            # Never retried aggressively: the site asked us to stop.
            self.store.record_anomalies(
                instrument_id=instrument.id,
                ticker=instrument.ticker,
                job_type="market_history",
                rejections=[],
            )
            self.queue.finish(
                checkpoint,
                status=STATUS_BLOCKED,
                error=page.error or "blocked by the site",
                retry_in=self.queue.backoff(checkpoint.attempts + 2),
            )
            self.coverage.measure(instrument, self.window, status=UNAVAILABLE,
                                  details={"blocked": True})
            self.session.commit()
            return {**summary, "status": STATUS_BLOCKED, "blocked": True}

        reference = self.store.last_validated_price(instrument.id)
        observations = validate_observations(
            result.observations, expected_ticker=instrument.ticker, reference_price=reference
        )
        trades = validate_trades(result.trades)

        source_url = result.source_urls[0] if result.source_urls else instrument.kase_url
        rejections = [*observations.rejections, *trades.rejections]
        if rejections:
            summary["anomalies"] = self.store.record_anomalies(
                instrument_id=instrument.id,
                ticker=instrument.ticker,
                job_type="market_history",
                rejections=rejections,
                source="kase_public_website",
                source_url=source_url,
                severity="critical" if not observations.ok else "high",
            )
        if not observations.ok:
            # The parse looked broken as a whole. Keep the previously validated
            # history exactly as it is and try again later.
            self.queue.finish(
                checkpoint,
                status=STATUS_FAILED,
                error="parser produced implausible data; batch rejected",
                retry_in=self.queue.backoff(checkpoint.attempts),
            )
            self.coverage.measure(instrument, self.window, status=FAILED,
                                  details={"parser_rejected": True})
            self.session.commit()
            return {**summary, "status": STATUS_FAILED, "error": "batch rejected"}

        summary["observations"] = self.store.save_observations(
            instrument.id, observations.accepted
        )
        summary["trades"] = self.store.save_trades(instrument.id, trades.accepted)
        summary["dividends"] = self.store.save_dividends(instrument.id, result.dividends)
        summary["reports"] = self.store.save_reports(instrument.id, result.reports)
        # News carries the corporate-action history with it: splits, buybacks
        # and dividend decisions are read from the publications that announced
        # them, never inferred from the price series.
        summary["news"] = self.store.save_news(
            instrument.id,
            result.news,
            ticker=instrument.ticker,
            issuer_code=self._issuer_code(instrument),
        )

        self.store.rebuild_daily_snapshots(instrument.id)

        latest = max(
            (record.observed_at for record in observations.accepted),
            default=checkpoint.last_processed_timestamp,
        )
        self.queue.advance(checkpoint, last_timestamp=latest)

        coverage = self.coverage.measure(
            instrument,
            self.window,
            details={"pages_visited": result.pages_visited, "notes": result.notes},
        )
        status = STATUS_COMPLETED if coverage.status == "complete" else STATUS_PARTIAL
        self.queue.finish(checkpoint, status=status)
        self.session.commit()

        return {
            **summary,
            "status": status,
            "coverage": CoverageService.to_dict(coverage),
            "pages_visited": result.pages_visited,
        }

    # -- a batch ----------------------------------------------------------

    async def run_batch(self, *, limit: int | None = None) -> dict:
        """One scheduled pass: a handful of instruments, politely.

        Concurrency is capped by ``BACKFILL_BROWSER_CONCURRENCY`` (default 1),
        because launching a browser per stock would both exhaust the machine and
        hammer a public website.
        """
        checkpoints = self.queue.next_batch(limit=limit)
        if not checkpoints:
            return {"processed": 0, "results": [], **self.queue.counts()}

        semaphore = asyncio.Semaphore(max(settings.BACKFILL_BROWSER_CONCURRENCY, 1))
        results: list[dict] = []

        async def one(checkpoint: BackfillCheckpoint) -> None:
            instrument = self.session.get(Instrument, checkpoint.instrument_id)
            if instrument is None:
                self.queue.finish(checkpoint, status=STATUS_FAILED, error="instrument missing")
                return
            async with semaphore:
                results.append(await self.backfill_instrument(instrument, checkpoint=checkpoint))

        # Sequential by default; the semaphore is what makes a higher setting
        # safe rather than the gather itself.
        for checkpoint in checkpoints:
            await one(checkpoint)

        return {
            "processed": len(results),
            "results": results,
            "queue": self.queue.counts(),
            "window": self.window.to_dict(),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }


__all__ = ["BackfillRunner", "HistorySource"]
