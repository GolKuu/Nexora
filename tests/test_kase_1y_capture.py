from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.jobs.capture_kase_1y_charts import (
    ManifestEntry,
    collect_history,
    discover_universe,
    write_manifest,
    write_report,
)
from app.models.bond import Bond
from app.models.history import (
    BackfillCheckpoint,
    DailyMarketSnapshot,
    HistoricalCoverage,
    MarketObservation,
)
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.providers.base import Provenance, ProviderBond
from app.services.backfill.records import ObservationRecord
from app.services.backfill.window import backfill_window
from app.services.series_service import PublicSeriesService


@pytest.fixture(autouse=True)
def clean_capture_fixture(session):
    """The production collector commits checkpoints; keep this shared DB clean."""
    yield
    instrument = session.scalar(select(Instrument).where(Instrument.ticker == "CAPTb1"))
    if instrument is not None:
        session.query(BackfillCheckpoint).filter_by(instrument_id=instrument.id).delete()
        session.query(HistoricalCoverage).filter_by(instrument_id=instrument.id).delete()
        session.query(DailyMarketSnapshot).filter_by(instrument_id=instrument.id).delete()
        session.query(MarketObservation).filter_by(instrument_id=instrument.id).delete()
        session.delete(instrument)
    bond = session.scalar(select(Bond).where(Bond.ticker == "CAPTb1"))
    if bond is not None:
        issuer_id = bond.issuer_id
        session.delete(bond)
        session.flush()
        issuer = session.get(Issuer, issuer_id)
        if issuer is not None and not issuer.bonds and not issuer.instruments:
            session.delete(issuer)
    session.commit()


class FakeProvider:
    async def get_bonds(self):
        now = datetime.now(timezone.utc)
        return [
            ProviderBond(
                ticker="CAPTb1", name="Capture issuer", issuer_code="CAPT",
                isin="KZ2C00000001", currency="KZT", nominal=1000,
                maturity_date=now.date() + timedelta(days=500),
                bond_type="corporate",
                kase_url="https://kase.kz/ru/investors/bonds/CAPTb1",
                provenance=Provenance(
                    source="kase_public_api",
                    source_url="https://kase.kz/api/instruments/securities/",
                    source_timestamp=now, fetched_at=now,
                ),
            )
        ]


class FakeChartClient:
    def __init__(self):
        self.calls = 0

    async def daily_history(self, ticker, window):
        self.calls += 1
        first = window.end - timedelta(days=3)
        return [
            ObservationRecord(
                observed_at=first, trading_date=first.date(),
                price=101.2, close=101.2, open=100.5, high=102.0, low=100.0,
                volume=25, source="kase_public_chart_api",
                source_url=f"https://kase.kz/tv-charts/securities/history?symbol={ticker}",
                source_timestamp=first, parser_version="fixture-v1", data_mode="public_api",
            ),
            ObservationRecord(
                observed_at=window.end - timedelta(days=1),
                trading_date=(window.end - timedelta(days=1)).date(),
                price=102.0, close=102.0, open=101.2, high=102.5, low=101.0,
                volume=30, source="kase_public_chart_api",
                source_url=f"https://kase.kz/tv-charts/securities/history?symbol={ticker}",
                source_timestamp=window.end - timedelta(days=1),
                parser_version="fixture-v1", data_mode="public_api",
            ),
        ]


@pytest.mark.anyio
async def test_bond_discovery_creates_shared_instrument_identity(session, anyio_backend):
    entries = await discover_universe(
        session, stocks=False, bonds=True, provider=FakeProvider()
    )
    assert [(row.ticker, row.isin, row.instrument_type) for row in entries] == [
        ("CAPTb1", "KZ2C00000001", "bond")
    ]
    assert session.scalar(select(Bond).where(Bond.ticker == "CAPTb1")) is not None
    identity = session.scalar(select(Instrument).where(
        Instrument.instrument_type == "bond", Instrument.ticker == "CAPTb1"
    ))
    assert identity is not None
    assert identity.kase_url.endswith("/CAPTb1")


@pytest.mark.anyio
async def test_history_collection_is_validated_covered_and_idempotent(session, anyio_backend):
    entry = (await discover_universe(
        session, stocks=False, bonds=True, provider=FakeProvider()
    ))[0]
    client = FakeChartClient()
    window = backfill_window(years=1)

    await collect_history(session, entry, window, client)
    await collect_history(session, entry, window, client)

    instrument = session.scalar(select(Instrument).where(
        Instrument.instrument_type == "bond", Instrument.ticker == "CAPTb1"
    ))
    assert session.scalar(select(func.count()).select_from(MarketObservation).where(
        MarketObservation.instrument_id == instrument.id
    )) == 2
    assert session.scalar(select(func.count()).select_from(DailyMarketSnapshot).where(
        DailyMarketSnapshot.instrument_id == instrument.id
    )) == 2
    coverage = session.scalar(select(HistoricalCoverage).where(
        HistoricalCoverage.instrument_id == instrument.id,
        HistoricalCoverage.job_type == "kase_1y_capture",
    ))
    assert coverage.status == "partial"
    assert entry.history_status == "PARTIAL"
    assert entry.insufficient_history is True
    assert coverage.details["price_unit"] == "%_of_nominal"
    series = PublicSeriesService(session).bond("CAPTb1", days=30)
    assert [row["close"] for row in series["sessions"]] == [101.2, 102.0]
    assert series["price_unit"] == "% от номинала"


def test_manifest_and_report_preserve_exact_counts(tmp_path, monkeypatch):
    root = tmp_path / "capture"
    report_path = tmp_path / "report.md"
    monkeypatch.setattr("app.jobs.capture_kase_1y_charts.REPORT_PATH", report_path)
    entries = [
        ManifestEntry(
            ticker="AAA", isin="KZ1", name="A", issuer="A",
            instrument_type="stock", instrument_subtype="ordinary",
            currency="KZT", catalog_url="https://kase.kz/stocks",
            instrument_url="https://kase.kz/AAA", capture_status="SUCCESS",
            history_status="COMPLETE", chart_status="SUCCESS", data_points=250,
        ),
        ManifestEntry(
            ticker="BBBb1", isin=None, name="B", issuer="B",
            instrument_type="bond", instrument_subtype="corporate",
            currency="KZT", catalog_url="https://kase.kz/bonds",
            instrument_url="https://kase.kz/BBBb1", capture_status="FAILED",
            history_status="UNAVAILABLE", chart_status="NO_HISTORY",
            error_reason="NO_PUBLIC_HISTORY",
        ),
    ]
    write_manifest(entries, root)
    report = write_report(entries, root)

    assert json.loads((root / "instruments.json").read_text(encoding="utf-8"))[0]["ticker"] == "AAA"
    assert (root / "instruments.csv").exists()
    assert report["stocks"]["discovered"] == 1
    assert report["stocks"]["screenshots_completed"] == 1
    assert report["bonds"]["unavailable"] == 1
    assert "BBBb1" in report_path.read_text(encoding="utf-8")
