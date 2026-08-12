"""A deliberately small in-process scheduler.

Celery or APScheduler would be a heavier dependency than this project needs:
one periodic job, one process. Swap it out when the workload justifies it.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.core.logging import get_logger
from app.jobs.refresh import refresh_quotes

logger = get_logger(__name__)


class PeriodicRefresh:
    def __init__(self, interval_seconds: float = 900.0):
        self.interval = interval_seconds
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.interval)
                result = await refresh_quotes()
                logger.info("scheduled quote refresh: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("scheduled refresh failed: %s", exc)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("periodic refresh started, interval=%ss", self.interval)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
