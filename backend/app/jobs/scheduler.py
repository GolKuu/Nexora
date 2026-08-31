"""Small multi-cadence scheduler for change-aware ingestion jobs.

The jobs here are I/O heavy in a way asyncio cannot hide: they open synchronous
SQLAlchemy sessions, commit to SQLite, download PDFs and parse them. Running
them on the API's own event loop meant a single collection pass froze every
request for as long as it lasted - the reason a normal page load appeared to
"wait for KASE". So the whole scheduler lives in a dedicated worker thread with
its own event loop. The API loop never runs a collector, and a slow KASE pass
can no longer delay a reader.

One worker thread, not one per job: the jobs already assumed they never ran
truly in parallel, and a single writer keeps SQLite out of lock contention.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.jobs.refresh import (
    refresh_ai_changes, refresh_catalog_incremental, refresh_documents,
    refresh_forecast_models, refresh_historical_backfill, refresh_monitoring,
    refresh_news, refresh_quotes, refresh_stocks,
)

logger = get_logger(__name__)


Job = tuple[str, float, Callable[[], Awaitable[dict]]]


class PeriodicRefresh:
    """Independent loops prevent a slow catalogue run delaying quote checks."""

    def __init__(self, interval_seconds: float | None = None):
        self.jobs: list[Job] = [
            ("quotes", interval_seconds or settings.SCHEDULE_QUOTES_SECONDS, refresh_quotes),
            ("stock_forecasts", interval_seconds or settings.STOCK_MARKET_REFRESH_SECONDS, refresh_stocks),
            ("catalog", settings.SCHEDULE_CATALOG_SECONDS, refresh_catalog_incremental),
            ("documents", settings.SCHEDULE_DOCUMENTS_SECONDS, refresh_documents),
            *(([("news", settings.SCHEDULE_NEWS_SECONDS, refresh_news)]) if settings.NEWS_COLLECTION_ENABLED else []),
            ("ai_changes", settings.SCHEDULE_AI_TASKS_SECONDS, refresh_ai_changes),
            ("forecast_training", settings.SCHEDULE_FORECAST_TRAINING_SECONDS, refresh_forecast_models),
            # History: a polite backfill pass, and the ten-minute cadence that
            # keeps extending the record once the backfill has caught up.
            *(
                [("historical_backfill", settings.SCHEDULE_BACKFILL_SECONDS, refresh_historical_backfill)]
                if settings.HISTORICAL_BACKFILL_ENABLED else []
            ),
            ("monitoring", settings.MONITORING_INTERVAL_SECONDS, refresh_monitoring),
        ]
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    async def _initial_market_refresh(self) -> None:
        """Populate market data once on startup without blocking FastAPI.

        The recurring loops deliberately sleep before each pass.  This separate
        warm-up avoids serving a stale snapshot for the first ten minutes while
        keeping the application startup responsive.  The steps are sequential
        so SQLite and KASE never receive three simultaneous startup jobs.
        """
        initial_jobs = {"quotes", "stock_forecasts", "monitoring"}
        for name, _interval, action in self.jobs:
            if name not in initial_jobs:
                continue
            try:
                result = await action()
                logger.info("startup %s refresh: %s", name, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("startup %s refresh failed: %s", name, exc)

    async def _loop_job(self, name: str, interval: float, action: Callable[[], Awaitable[dict]]) -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                result = await action()
                logger.info("scheduled %s refresh: %s", name, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("scheduled %s refresh failed: %s", name, exc)

    async def _main(self) -> None:
        tasks = [
            asyncio.create_task(self._initial_market_refresh(), name="kase-startup-market"),
            *[
                asyncio.create_task(self._loop_job(name, interval, action), name=f"kase-{name}")
                for name, interval, action in self.jobs
            ],
        ]
        logger.info(
            "incremental scheduler started on worker thread: %s",
            {name: seconds for name, seconds, _ in self.jobs},
        )
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            raise

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_until_complete(self._main())
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("scheduler thread stopped: %s", exc)
        finally:
            # Cancel whatever is still pending so the loop can close cleanly.
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            with contextlib.suppress(Exception):
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._loop = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="kase-scheduler", daemon=True
        )
        self._thread.start()
        # Wait only for the loop to exist, never for a collection pass.
        self._ready.wait(timeout=10.0)

    async def stop(self) -> None:
        thread, loop = self._thread, self._loop
        self._thread = None
        if thread is None:
            return
        if loop is not None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(
                    lambda: [t.cancel() for t in asyncio.all_tasks(loop)]
                )
        # Joining is blocking work; keep it off the API event loop.
        await asyncio.to_thread(thread.join, 15.0)
        if thread.is_alive():  # pragma: no cover - shutdown race
            logger.warning("scheduler thread did not stop within 15s")
