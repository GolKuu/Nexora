"""Live backfill against the real public kase.kz.

Disabled by default. Enable deliberately:

    RUN_LIVE_KASE_BACKFILL_TESTS=true pytest -m live_backfill

The test visits kase.kz as an ordinary public visitor: no API key, no login, no
CAPTCHA solving. If the site declines to serve us, that is a *skip*, not a
failure - and certainly not a reason to try harder.

What it proves, and nothing more: a real stock can be discovered, its official
page opened, whatever history is exposed stored with its source URL and
timestamp, and the whole thing re-run without creating duplicates. It does not
claim a complete two-year history, because that is not ours to promise.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from app.models.history import MarketObservation
from app.models.instrument import Instrument
from app.models.stock import Stock
from app.services.backfill.collector import KaseHistoryCollector
from app.services.backfill.runner import BackfillRunner
from app.services.backfill.window import backfill_window

pytestmark = [
    pytest.mark.live_backfill,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_KASE_BACKFILL_TESTS", "").lower() not in ("1", "true", "yes"),
        reason="set RUN_LIVE_KASE_BACKFILL_TESTS=true to hit the real kase.kz",
    ),
]


@pytest.mark.anyio
async def test_live_backfill_stores_real_history_without_duplicates(session, anyio_backend):
    from app.collectors.kase_stock_catalog import KaseStockCatalogCollector

    # 1. discover a real stock from the public catalogue - no hardcoded ticker
    discovery = await KaseStockCatalogCollector(session).collect()
    assert discovery.get("created", 0) + discovery.get("updated", 0) > 0, discovery

    row = session.execute(
        select(Instrument, Stock)
        .join(Stock, Stock.instrument_id == Instrument.id)
        .where(Instrument.instrument_type == "stock", Instrument.is_active.is_(True))
        .limit(1)
    ).first()
    assert row is not None, "the public catalogue exposed no active share"
    instrument, _stock = row

    # 2-5. open its official page and store whatever history it exposes
    window = backfill_window()
    runner = BackfillRunner(session, collector=KaseHistoryCollector(), window=window)
    first = await runner.backfill_instrument(instrument)

    if first["status"] == "blocked":
        pytest.skip("kase.kz declined the request; not attempting to work around it")

    stored = list(
        session.execute(
            select(MarketObservation).where(
                MarketObservation.instrument_id == instrument.id
            )
        ).scalars()
    )
    if not stored:
        pytest.skip(
            "the public page exposed no machine-readable history for "
            f"{instrument.ticker}; nothing is fabricated to compensate"
        )

    # 6. provenance travels with every stored value
    for observation in stored:
        assert observation.source
        assert observation.source_url and "kase.kz" in observation.source_url
        assert observation.source_timestamp is not None
        assert observation.fetched_at is not None
        assert observation.parser_version

    # 7. a second run adds nothing
    before = len(stored)
    second = await runner.backfill_instrument(instrument)
    after = session.execute(
        select(MarketObservation).where(MarketObservation.instrument_id == instrument.id)
    ).scalars()
    assert len(list(after)) == before
    assert second["observations"]["created"] == 0
    # Publications and report documents are re-read on every pass, so the second
    # run is where their fingerprints have to hold.
    for section in ("trades", "dividends", "reports", "news"):
        assert second[section]["created"] == 0, f"{section} duplicated on re-run"

    # Coverage is reported as measured, never rounded up to "two years".
    coverage = second.get("coverage") or {}
    assert coverage.get("status") in ("complete", "partial", "unavailable")
    if coverage.get("completeness") is not None:
        assert 0.0 <= coverage["completeness"] <= 1.0
