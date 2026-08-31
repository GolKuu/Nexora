"""The KASE scheduler starts promptly and keeps the ten-minute cadence."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.jobs.refresh import _close_provider
from app.jobs.scheduler import PeriodicRefresh


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_start_runs_market_warmup_in_order_on_a_worker_thread():
    calls: list[str] = []
    done = threading.Event()

    async def action(name: str) -> dict:
        calls.append(name)
        if name == "monitoring":
            done.set()
        return {"status": "ok"}

    scheduler = PeriodicRefresh(interval_seconds=600)
    scheduler.jobs = [
        ("quotes", 600, lambda: action("quotes")),
        ("stock_forecasts", 600, lambda: action("stock_forecasts")),
        ("monitoring", 600, lambda: action("monitoring")),
    ]

    scheduler.start()
    try:
        assert done.wait(timeout=5), "startup warm-up did not run"
        assert calls == ["quotes", "stock_forecasts", "monitoring"]
        # The collectors must never occupy the loop that serves requests.
        assert scheduler._thread is not None
        assert scheduler._thread is not threading.current_thread()
        assert scheduler._thread.daemon
    finally:
        await scheduler.stop()
    assert scheduler._thread is None


async def test_a_slow_collector_does_not_block_the_api_event_loop():
    """The regression that made a page load wait for a KASE pass."""
    started = threading.Event()

    async def slow() -> dict:
        started.set()
        # Synchronous I/O is exactly what the real collectors do.
        time.sleep(2.0)
        return {"status": "ok"}

    scheduler = PeriodicRefresh(interval_seconds=600)
    scheduler.jobs = [("quotes", 600, slow)]

    scheduler.start()
    try:
        assert started.wait(timeout=5)
        # While the collector sleeps, the calling loop must stay free.
        begin = time.monotonic()
        for _ in range(10):
            await asyncio.sleep(0.01)
        elapsed = time.monotonic() - begin
        assert elapsed < 1.0, f"event loop was blocked for {elapsed:.2f}s"
    finally:
        await scheduler.stop()


async def test_default_market_jobs_run_every_ten_minutes():
    jobs = {name: interval for name, interval, _action in PeriodicRefresh().jobs}

    assert jobs["quotes"] == 600
    assert jobs["stock_forecasts"] == 600
    assert jobs["monitoring"] == 600


async def test_startup_failure_does_not_prevent_the_remaining_market_jobs():
    calls: list[str] = []

    async def broken() -> dict:
        calls.append("quotes")
        raise RuntimeError("KASE unavailable")

    async def healthy(name: str) -> dict:
        calls.append(name)
        return {"status": "ok"}

    scheduler = PeriodicRefresh(interval_seconds=600)
    scheduler.jobs = [
        ("quotes", 600, broken),
        ("stock_forecasts", 600, lambda: healthy("stock_forecasts")),
        ("monitoring", 600, lambda: healthy("monitoring")),
    ]

    await scheduler._initial_market_refresh()

    assert calls == ["quotes", "stock_forecasts", "monitoring"]


async def test_one_shot_provider_closes_nested_browser_sessions():
    closed: list[str] = []

    class Provider:
        def __init__(self, name: str, providers=()):
            self.name = name
            self.providers = providers

        async def aclose(self) -> None:
            closed.append(self.name)

    provider = Provider("composite", [Provider("official"), Provider("browser")])

    await _close_provider(provider)

    assert closed == ["official", "browser", "composite"]
