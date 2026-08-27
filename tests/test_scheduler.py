"""The KASE scheduler starts promptly and keeps the ten-minute cadence."""

from __future__ import annotations

import asyncio

import pytest

from app.jobs.refresh import _close_provider
from app.jobs.scheduler import PeriodicRefresh


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_start_runs_market_warmup_in_order_and_creates_recurring_tasks():
    calls: list[str] = []

    async def action(name: str) -> dict:
        calls.append(name)
        return {"status": "ok"}

    scheduler = PeriodicRefresh(interval_seconds=600)
    scheduler.jobs = [
        ("quotes", 600, lambda: action("quotes")),
        ("stock_forecasts", 600, lambda: action("stock_forecasts")),
        ("monitoring", 600, lambda: action("monitoring")),
    ]

    scheduler.start()
    await asyncio.wait_for(scheduler._tasks[0], timeout=1)

    assert calls == ["quotes", "stock_forecasts", "monitoring"]
    assert {task.get_name() for task in scheduler._tasks} == {
        "kase-startup-market",
        "kase-quotes",
        "kase-stock_forecasts",
        "kase-monitoring",
    }
    assert all(interval == 600 for _, interval, _ in scheduler.jobs)

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
