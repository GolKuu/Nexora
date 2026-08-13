"""Politeness controls for the browser agent (§23).

A browser agent that hammers a public exchange site is a browser agent that
gets blocked - and deserves to be. Three mechanisms, all process-wide:

* a semaphore capping how many pages navigate at once;
* a minimum interval between navigations;
* exponential backoff with jitter for retries.
"""

from __future__ import annotations

import asyncio
import random
import time

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RequestPacer:
    """Serialises navigations so the site sees a human-plausible rhythm."""

    def __init__(
        self,
        *,
        min_interval_ms: int | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self.min_interval = (
            min_interval_ms
            if min_interval_ms is not None
            else settings.BROWSER_MIN_INTERVAL_MS
        ) / 1000.0
        self._semaphore = asyncio.Semaphore(
            max_concurrency
            if max_concurrency is not None
            else max(1, settings.BROWSER_MAX_CONCURRENCY)
        )
        self._lock = asyncio.Lock()
        self._last_at = 0.0

    async def acquire(self) -> None:
        await self._semaphore.acquire()
        async with self._lock:
            wait = self._last_at + self.min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_at = time.monotonic()

    def release(self) -> None:
        self._semaphore.release()

    class _Slot:
        def __init__(self, pacer: "RequestPacer") -> None:
            self._pacer = pacer

        async def __aenter__(self) -> None:
            await self._pacer.acquire()

        async def __aexit__(self, *_exc) -> None:
            self._pacer.release()

    def slot(self) -> "RequestPacer._Slot":
        return RequestPacer._Slot(self)


def backoff_delay(attempt: int, *, base_ms: int | None = None) -> float:
    """Exponential backoff with full jitter, in seconds."""
    base = (base_ms if base_ms is not None else settings.BROWSER_BACKOFF_BASE_MS) / 1000.0
    return random.uniform(0.0, base * (2 ** max(0, attempt - 1)))


class RuntimeBudget:
    """A wall-clock budget for one flow (§10: ``max_runtime``)."""

    def __init__(self, seconds: float | None = None) -> None:
        self.seconds = seconds if seconds is not None else settings.BROWSER_MAX_RUNTIME_S
        self._started = time.monotonic()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    @property
    def remaining(self) -> float:
        return max(0.0, self.seconds - self.elapsed)

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0.0

    def check(self, what: str) -> bool:
        """Log once and report whether there is still time for ``what``."""
        if self.exhausted:
            logger.info("browser runtime budget exhausted before %s", what)
            return False
        return True


#: Shared by every session in the process, so N sessions cannot multiply load.
global_pacer = RequestPacer()
