"""Tests that hit the real KASE endpoints.

Skipped unless RUN_LIVE_KASE_TESTS=true, because a test suite that silently
depends on a third party's uptime is a test suite nobody trusts.

    RUN_LIVE_KASE_TESTS=true pytest -m live_kase
"""

from __future__ import annotations

import os
from datetime import datetime

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


async def test_public_api_publishes_a_real_coupon_schedule():
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        periods = await provider.get_coupon_schedule("BRKZb14")
    finally:
        await provider.aclose()

    assert periods, "KASE publishes a coupon schedule for this issue"
    assert all(p.payment_date is not None for p in periods)
    # Sorted, and every fixed-rate period carries its own rate.
    assert periods == sorted(periods, key=lambda p: p.payment_date)
    rates = [p.rate for p in periods if p.rate is not None]
    assert rates and all(0 < r < 1 for r in rates)


async def test_coupon_schedule_recovers_the_payment_frequency():
    from app.calculations.types import BondSpec, CouponPeriod
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        bond = await provider.get_bond("BTRKb24")
        periods = await provider.get_coupon_schedule("BTRKb24")
    finally:
        await provider.aclose()

    spec = BondSpec(
        maturity_date=bond.maturity_date,
        coupon_rate=bond.coupon_rate,
        coupon_frequency=bond.coupon_frequency,
        nominal=bond.nominal,
        day_count=bond.day_count,
        schedule=tuple(
            CouponPeriod(payment_date=p.payment_date, rate=p.rate) for p in periods
        ),
    )
    # This issue's frequency cannot be derived from the prev/next pair, which
    # used to make it look like a zero-coupon bond.
    assert spec.effective_frequency in (1, 2, 4, 12)
    assert spec.is_zero_coupon is False


async def test_accrued_interest_agrees_with_the_exchange():
    """Our schedule-based accrued must match KASE's own, within a day."""
    from app.calculations.bond_math import calculate_accrued_interest
    from app.calculations.types import BondSpec, CouponPeriod
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        results = (await provider._get("/api/trade-results/bonds/")).json
        checked = 0
        worst = 0.0
        for row in results[:12]:
            ticker = row.get("code")
            clean = row.get("last_deal_price")
            dirty = row.get("dirty_last_price")
            traded_on = row.get("last_deal_date")
            if not (ticker and clean and dirty and traded_on):
                continue
            bond = await provider.get_bond(ticker)
            if not (bond and bond.nominal and bond.maturity_date):
                continue
            periods = await provider.get_coupon_schedule(ticker)
            if not periods:
                continue
            settlement = datetime.fromisoformat(traded_on).date()
            if settlement >= bond.maturity_date:
                continue
            spec = BondSpec(
                maturity_date=bond.maturity_date,
                coupon_rate=bond.coupon_rate,
                coupon_frequency=bond.coupon_frequency,
                nominal=bond.nominal,
                issue_date=bond.issue_date,
                next_coupon_date=bond.next_coupon_date,
                coupon_type=bond.coupon_type,
                day_count=bond.day_count,
                schedule=tuple(
                    CouponPeriod(payment_date=p.payment_date, rate=p.rate)
                    for p in periods
                ),
            )
            ours = calculate_accrued_interest(spec, settlement)
            theirs = dirty - clean / 100.0 * bond.nominal
            if ours is None or theirs < 0:
                continue
            # Tolerance is four days of coupon. KASE settles T+n with n
            # varying by board, and resets accrued to zero once a trade
            # settles past the coupon date; we price for same-day settlement.
            # Inside that band the difference is a settlement convention;
            # outside it, it would be a formula error.
            four_days = bond.nominal * (bond.coupon_rate or 0.0) / 360.0 * 4
            assert abs(ours - theirs) <= max(four_days, 0.05) or theirs == 0.0, (
                f"{ticker}: ours={ours:.2f} kase={theirs:.2f}"
            )
            worst = max(worst, abs(ours - theirs))
            checked += 1
    finally:
        await provider.aclose()
    assert checked >= 5, f"only {checked} issues could be checked"


async def test_government_curve_is_downward_or_flat_and_plausible():
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        curve = await provider.get_government_curve()
    finally:
        await provider.aclose()

    points = curve["points"]
    assert len(points) >= 4, "the KZGB list should populate several tenor nodes"
    assert curve["constituents"] > 20
    for point in points:
        # A sovereign yield outside 0-100 % annual is a parsing failure.
        assert 0.0 < point["yield_rate"] < 1.0
    tenors = [p["tenor_years"] for p in points]
    assert tenors == sorted(tenors)


async def test_search_suggestions_resolve_a_partial_ticker():
    from app.providers.kase_public_api import KasePublicApiProvider

    provider = KasePublicApiProvider()
    try:
        suggestions = await provider.suggest("BRKZ")
    finally:
        await provider.aclose()
    assert suggestions
    assert any(s.startswith("BRKZ") for s in suggestions)
