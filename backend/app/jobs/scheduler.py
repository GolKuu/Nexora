"""Small multi-cadence scheduler for change-aware ingestion jobs."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.core.logging import get_logger
from app.jobs.refresh import refresh_ai_changes, refresh_catalog_incremental, refresh_documents, refresh_news, refresh_quotes, refresh_stocks

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
        ]
        self._tasks: list[asyncio.Task] = []

    async def _loop(self, name: str, interval: float, action: Callable[[], Awaitable[dict]]) -> None:
        while True:
            try:
                await asyncio.sleep(interval)
                result = await action()
                logger.info("scheduled %s refresh: %s", name, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("scheduled %s refresh failed: %s", name, exc)

    def start(self) -> None:
        if not self._tasks:
            self._tasks = [
                asyncio.create_task(self._loop(name, interval, action), name=f"kase-{name}")
                for name, interval, action in self.jobs
            ]
            logger.info("incremental scheduler started: %s", {name: seconds for name, seconds, _ in self.jobs})

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
