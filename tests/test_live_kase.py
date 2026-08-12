"""Tests that hit the real KASE endpoints.

Skipped unless RUN_LIVE_KASE_TESTS=true, because a test suite that silently
depends on a third party's uptime is a test suite nobody trusts.

    RUN_LIVE_KASE_TESTS=true pytest -m live_kase
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.live_kase,
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_KASE_TESTS", "false").lower() != "true",
        reason="RUN_LIVE_KASE_TESTS is not enabled",
    ),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_website_probe_reports_a_real_result():
    """The probe must report what actually happened, success or failure."""
    from app.providers.kase_website import KaseWebsiteProvider

    provider = KaseWebsiteProvider()
    try:
        status = await provider.health()
    finally:
        await provider.aclose()

    assert status.is_mock is False
    assert status.detail
    if not status.reachable:
        pytest.skip(f"kase.kz is unreachable from here: {status.detail}")
    assert status.latency_ms is not None


async def test_official_api_probe_when_a_key_is_configured():
    from app.core.config import settings

    if not settings.KASE_API_KEY:
        pytest.skip("KASE_API_KEY is not configured")

    from app.providers.kase_api import KaseApiProvider

    provider = KaseApiProvider()
    try:
        status = await provider.health()
        assert status.is_mock is False
        if not status.reachable:
            pytest.skip(f"KASE API did not respond: {status.detail}")
        bonds = await provider.get_bonds()
        assert bonds, "the API answered but returned no bonds"
        assert all(b.provenance and b.provenance.source == "kase_api" for b in bonds)
    finally:
        await provider.aclose()
