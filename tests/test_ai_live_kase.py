"""The AI live-data overlay is host-restricted, read-only and cache-safe."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ai.tools.live_kase import LiveKaseStore
from ai.tools.store import SnapshotStore
from app.providers.base import ProviderBond, ProviderQuote, Provenance


def _provenance() -> Provenance:
    now = datetime.now(timezone.utc)
    return Provenance(
        source="kase_public_api",
        source_identifier="TESTb1",
        source_url="https://kase.kz/api/instruments/bonds/TESTb1/",
        source_timestamp=now,
        fetched_at=now,
        data_mode="end_of_day",
    )


def test_live_store_refuses_non_kase_hosts():
    with pytest.raises(ValueError, match="official"):
        LiveKaseStore(base_url="https://example.com")
    with pytest.raises(ValueError, match="official"):
        LiveKaseStore(base_url="http://kase.kz")


def test_live_store_overlays_snapshot_with_fresh_provenance(monkeypatch):
    store = LiveKaseStore(fallback=SnapshotStore())
    bond = ProviderBond(
        ticker="TESTb1",
        name="Test bond",
        issuer_code="TEST",
        nominal=1_000.0,
        maturity_date=date(2030, 1, 1),
        provenance=_provenance(),
    )
    quote = ProviderQuote(
        ticker="TESTb1",
        timestamp=datetime.now(timezone.utc),
        ask=101.25,
        ytm=0.173,
        provenance=_provenance(),
    )

    def fake_fetch(key, _operation):
        if key == "bond:TESTB1":
            store._mark(key, success=True)
            return bond
        if key == "quotes":
            store._mark(key, success=True)
            return [quote]
        return None

    monkeypatch.setattr(store, "_fetch", fake_fetch)

    live_bond = store.bond("testb1")
    live_quote = store.quote("testb1")
    assert live_bond["ticker"] == "TESTb1"
    assert live_bond["source_url"].startswith("https://kase.kz/")
    assert live_quote["ask"] == 101.25
    assert live_quote["data_mode"] == "end_of_day"


def test_live_store_falls_back_without_relabelling_snapshot(monkeypatch):
    fallback = SnapshotStore()
    store = LiveKaseStore(fallback=fallback)
    monkeypatch.setattr(store, "_fetch", lambda _key, _operation: None)

    expected = fallback.bond("HCBNb13")
    actual = store.bond("HCBNb13")
    assert actual == expected
    assert actual["fetched_at"] == expected["fetched_at"]
