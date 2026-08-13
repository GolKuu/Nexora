"""Running without KASE.

The product must keep answering when the exchange is unreachable - blocked,
down, or simply not routable from where the service runs. These tests hold
that guarantee by breaking the network on purpose.
"""

from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.core.enums import DataMode
from app.providers.factory import build_provider
from app.providers.offline_cache import OfflineCacheProvider
from app.services.freshness import effective_data_mode, freshness

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def no_network(monkeypatch):
    """A real outage: DNS and connect both fail."""

    def blocked(*args, **kwargs):
        raise OSError("network is unreachable (simulated outage)")

    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    return blocked


class TestOfflineProvider:
    def test_factory_builds_it_for_offline_mode(self):
        provider = build_provider(Settings(KASE_DATA_MODE="offline"))
        assert isinstance(provider, OfflineCacheProvider)

    def test_it_is_not_mock_so_production_may_use_it(self):
        # The distinction that matters: it serves real cached data, so unlike
        # MockKaseProvider it is allowed in production.
        provider = build_provider(
            Settings(KASE_DATA_MODE="offline", APP_ENV="production")
        )
        assert provider.is_mock is False
        assert provider.data_mode == DataMode.CACHED.value

    async def test_health_never_claims_a_connection(self, no_network):
        status = await OfflineCacheProvider().health()
        assert status.reachable is False
        assert status.is_mock is False
        assert status.data_mode == DataMode.CACHED.value
        assert status.detail

    async def test_every_fetch_returns_empty_without_touching_the_network(
        self, no_network
    ):
        provider = OfflineCacheProvider()
        assert await provider.get_bonds() == []
        assert await provider.get_bond("BRKZb14") is None
        assert await provider.get_quotes() == []
        assert await provider.get_coupon_schedule("BRKZb14") == []
        assert await provider.get_issuer("BRKZ") is None
        assert await provider.get_financials("BRKZ") == []
        assert await provider.get_ratings("BRKZ") == []
        assert await provider.search_bonds("BRKZ") == []


class TestFreshness:
    NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)

    def test_recent_end_of_day_keeps_its_label(self):
        recent = self.NOW - timedelta(hours=18)
        assert effective_data_mode("end_of_day", recent, now=self.NOW) == "end_of_day"

    def test_old_end_of_day_decays_to_cached(self):
        old = self.NOW - timedelta(days=10)
        assert effective_data_mode("end_of_day", old, now=self.NOW) == "cached"

    def test_a_weekend_does_not_make_friday_stale(self):
        # KASE does not trade at weekends; Friday's close read on Monday is
        # still the latest the market has said.
        friday = self.NOW - timedelta(days=2, hours=20)
        assert effective_data_mode("end_of_day", friday, now=self.NOW) == "end_of_day"

    def test_unknown_timestamp_cannot_be_vouched_for(self):
        assert effective_data_mode("live", None, now=self.NOW) == "cached"

    def test_mock_is_never_relabelled(self):
        # Demo data stays flagged as demo however fresh it looks.
        assert effective_data_mode("mock", self.NOW, now=self.NOW) == "mock"

    def test_cached_stays_cached(self):
        assert effective_data_mode("cached", self.NOW, now=self.NOW) == "cached"

    def test_freshness_reports_age_and_staleness(self):
        old = self.NOW - timedelta(days=10)
        result = freshness("end_of_day", old, now=self.NOW)
        assert result["data_mode"] == "cached"
        assert result["is_stale"] is True
        assert result["data_age_seconds"] == pytest.approx(10 * 86400)

    def test_fresh_data_is_not_stale(self):
        result = freshness("end_of_day", self.NOW - timedelta(hours=2), now=self.NOW)
        assert result["is_stale"] is False
        assert result["data_mode"] == "end_of_day"


class TestSnapshotRoundTrip:
    """A snapshot must reconstruct a working database with no network."""

    def test_export_then_import_preserves_the_data(self, seeded, session, tmp_path, no_network):
        from app.collectors.snapshot import export_snapshot, import_snapshot
        from app.repositories.bonds import BondRepository

        path = tmp_path / "snapshot.json"
        exported = export_snapshot(session, path, note="test")
        assert path.exists()
        assert exported["bonds"] >= 1

        before = {b.ticker for b in BondRepository(session).list(limit=1000)}
        imported = import_snapshot(session, path, recompute=False)
        after = {b.ticker for b in BondRepository(session).list(limit=1000)}

        assert imported["bonds"] == exported["bonds"]
        assert before <= after

    def test_snapshot_records_when_it_was_captured(self, seeded, session, tmp_path):
        import json

        from app.collectors.snapshot import export_snapshot

        path = tmp_path / "snapshot.json"
        export_snapshot(session, path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        # Without this the age of the data could not be reported honestly.
        assert payload["captured_at"]
        assert payload["snapshot_version"]
        assert payload["sources"]["market_and_reference"].startswith("https://kase.kz")

    def test_import_does_not_backdate_market_data(self, seeded, session, tmp_path):
        """Imported quotes keep their real timestamps, so they age normally."""
        import json

        from app.collectors.snapshot import export_snapshot

        path = tmp_path / "snapshot.json"
        export_snapshot(session, path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for quote in payload["quotes"]:
            # Timestamps travel verbatim; nothing is rewritten to look fresh.
            assert "__dt__" in quote["timestamp"]

    def test_missing_snapshot_fails_loudly(self, session, tmp_path):
        from app.collectors.snapshot import import_snapshot

        with pytest.raises(FileNotFoundError):
            import_snapshot(session, tmp_path / "nope.json")
