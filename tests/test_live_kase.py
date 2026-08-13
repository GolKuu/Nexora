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


async def test_public_api_health_is_a_real_probe():
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        status = await provider.health()
    finally:
        await provider.aclose()
    assert status.reachable is True
    assert status.is_mock is False
    assert status.latency_ms is not None


async def test_public_api_returns_a_real_catalog():
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        bonds = await provider.get_bonds()
    finally:
        await provider.aclose()
    # KASE listed ~2 180 bonds when this was written; a collapse to a handful
    # means the payload shape changed, not that the market vanished.
    assert len(bonds) > 1000
    assert all(bond.ticker for bond in bonds)


async def test_public_api_issue_parameters_carry_isin_and_coupon():
    from app.providers.kase_public_api import ISIN_RE, KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        bond = await provider.get_bond("BRKZb14")
    finally:
        await provider.aclose()
    assert bond is not None
    assert ISIN_RE.match(bond.isin or "")
    assert bond.coupon_rate is not None and 0 < bond.coupon_rate < 1
    assert bond.coupon_frequency in (1, 2, 4, 12)
    assert bond.nominal and bond.nominal > 0
    assert bond.maturity_date is not None


async def test_public_api_quotes_normalise_to_percent_of_nominal():
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        quotes = await provider.get_quotes()
        nominals = {}
        for quote in quotes[:5]:
            bond = await provider.get_bond(quote.ticker)
            if bond and bond.nominal:
                nominals[quote.ticker] = bond.nominal
        priced = await provider.get_quotes(list(nominals), nominals=nominals)
    finally:
        await provider.aclose()

    assert priced
    for quote in priced:
        if quote.accrued_interest is None:
            continue
        # Accrued interest is a modest share of nominal, never a money amount
        # leaking through on the percentage scale.
        assert -1.0 <= quote.accrued_interest < 30.0
        if quote.ytm is not None:
            assert -0.5 < quote.ytm < 2.0  # decimal, not percent


async def test_benchmark_curve_is_available_for_credit_spreads():
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        curve = await provider.get_benchmark_curve()
    finally:
        await provider.aclose()
    # The government curve is what credit spread is measured against.
    assert curve["medium"] is not None
    assert 0.0 < curve["medium"] < 1.0


async def test_official_inflation_is_readable_from_stat_gov(session):
    """The primary source really is parseable, end to end and into the DB."""
    from app.collectors.inflation_collector import StatGovInflationCollector

    collector = StatGovInflationCollector(session)
    try:
        result = await collector.fetch_latest()
    finally:
        await collector.aclose()
    assert result["ok"] is True, result.get("detail")
    # An annual CPI print outside 0-100 % is a parsing failure, not news.
    assert 0.0 < result["annual_rate"] < 1.0
    assert result["source"] == "stat.gov.kz"
    assert result["source_url"].startswith("https://stat.gov.kz/")
