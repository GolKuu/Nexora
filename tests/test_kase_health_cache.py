"""The KASE connectivity probe must not sit in every page load.

`DataModeBanner` renders in the application layout, so `/health/kase` is hit on
every page view. Each call used to perform a real outbound request to KASE,
which put an external round trip - and its timeout - on the critical path of
every page. The probe answers a question that does not change between two page
views a second apart, so it is memoised behind a short shared TTL.
"""

from __future__ import annotations

import pytest

from app.services import health_service


@pytest.fixture(autouse=True)
def _clear_cache():
    health_service.reset_kase_health_cache()
    yield
    health_service.reset_kase_health_cache()


class _CountingProvider:
    name = "kase_public_api"
    is_mock = False
    sub_statuses: tuple = ()

    def __init__(self) -> None:
        self.calls = 0

    async def health(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        self.calls += 1
        return SimpleNamespace(
            name=self.name, reachable=True, is_mock=False, data_mode="end_of_day",
            checked_at=datetime.now(timezone.utc), latency_ms=12.0, detail="ok",
        )


@pytest.mark.anyio
async def test_repeated_probes_hit_kase_once(monkeypatch):
    provider = _CountingProvider()
    monkeypatch.setattr(health_service, "get_provider", lambda: provider)

    first = await health_service.kase_health()
    second = await health_service.kase_health()
    third = await health_service.kase_health()

    assert provider.calls == 1, "every page view made its own outbound request"
    assert first["cached"] is False
    assert second["cached"] is True and third["cached"] is True
    # The verdict is reused, never re-dated: a cached answer says how old it is.
    assert second["checked_at"] == first["checked_at"]
    assert second["cache_age_seconds"] >= 0


@pytest.mark.anyio
async def test_an_expired_entry_probes_again(monkeypatch):
    provider = _CountingProvider()
    monkeypatch.setattr(health_service, "get_provider", lambda: provider)
    monkeypatch.setattr(health_service, "KASE_HEALTH_TTL_SECONDS", 0.0)

    await health_service.kase_health()
    await health_service.kase_health()

    assert provider.calls == 2


@pytest.mark.anyio
async def test_a_failing_probe_is_also_cached(monkeypatch):
    """An unreachable KASE must not make every page pay the timeout again."""

    class Failing(_CountingProvider):
        async def health(self):
            self.calls += 1
            raise RuntimeError("kase unreachable")

    provider = Failing()
    monkeypatch.setattr(health_service, "get_provider", lambda: provider)

    first = await health_service.kase_health()
    second = await health_service.kase_health()

    assert provider.calls == 1
    assert first["connected"] is False
    assert second["cached"] is True
    assert "kase unreachable" in second["error"]
