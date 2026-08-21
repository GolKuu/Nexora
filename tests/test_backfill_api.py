"""Chart, per-stock history status and the operational backfill endpoints."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.forecast.calendar import KASE_TZ
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock
from app.services.backfill.records import ObservationRecord, STATUS_TRADED
from app.services.backfill.store import HistoryStore
from app.services.backfill.window import market_days, shift_years


@pytest.fixture
def seeded_stock(session):
    """A stock carrying two years of stored daily history."""
    row = session.query(Instrument).filter(
        Instrument.instrument_type == "stock"
    ).first()
    if row is None:
        issuer = Issuer(name="Chart Test Issuer", code="CHARTISS", sector="corporate")
        session.add(issuer)
        session.flush()
        row = Instrument(
            ticker="CHARTTST", isin="KZCHARTTST01", issuer_id=issuer.id,
            instrument_type="stock", currency="KZT", is_active=True,
            kase_url="https://kase.kz/en/investors/bonds/CHARTTST",
        )
        session.add(row)
        session.flush()
        session.add(Stock(instrument_id=row.id))
        session.flush()

    store = HistoryStore(session)
    until = date(2026, 8, 14)
    days = market_days(shift_years(until, 2), until)
    store.save_observations(
        row.id,
        [
            ObservationRecord(
                observed_at=datetime.combine(
                    day, datetime.min.time(), tzinfo=KASE_TZ
                ).replace(hour=17).astimezone(timezone.utc),
                price=1000.0 + index * 0.1,
                volume=100.0,
                status=STATUS_TRADED,
                source="kase_public_website",
                source_url="https://kase.kz/en/investors/bonds/DEMO",
                data_mode="browser",
                trading_date=day,
            )
            for index, day in enumerate(days)
        ],
    )
    store.rebuild_daily_snapshots(row.id)
    session.commit()
    return row


def test_chart_endpoint_serves_stored_history(api, seeded_stock):
    body = api.get(f"/stocks/{seeded_stock.ticker}/chart", params={"range": "2y"}).json()

    assert body["range"] == "2y"
    assert body["resolution"] == "1w"
    assert body["source"] == "daily_market_snapshots"
    assert body["series"], body
    assert body["instrument"]["ticker"] == seeded_stock.ticker
    assert "coverage" in body and "insufficient_history" in body
    assert body["last_updated"]


@pytest.mark.parametrize(
    "range_key", ["1d", "5d", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "max"]
)
def test_every_range_is_supported(api, seeded_stock, range_key):
    body = api.get(
        f"/stocks/{seeded_stock.ticker}/chart", params={"range": range_key}
    ).json()
    assert body["range"] == range_key
    assert isinstance(body["series"], list)


def test_events_can_be_excluded(api, seeded_stock):
    body = api.get(
        f"/stocks/{seeded_stock.ticker}/chart",
        params={"range": "1y", "include_events": False},
    ).json()
    assert body["events"] == []


def test_bad_range_is_rejected(api, client, seeded_stock):
    response = client.get(
        f"/api/v1/stocks/{seeded_stock.ticker}/chart", params={"range": "10y"}
    )
    assert response.status_code == 422


def test_history_status_reports_coverage_per_stock(api, seeded_stock):
    body = api.get(f"/stocks/{seeded_stock.ticker}/history-status").json()

    assert body["instrument"]["ticker"] == seeded_stock.ticker
    assert body["window"]["years"] == 2
    assert body["stored"]["daily_snapshots"] > 0
    assert body["stored"]["traded_days"] > 0
    assert body["backfill"]["status"] in (
        "not_queued", "queued", "processing", "completed", "partial", "failed", "blocked"
    )
    assert body["oldest_observation"] and body["latest_observation"]


def test_admin_status_counts_the_stored_history(api, seeded_stock):
    body = api.get("/admin/backfill/status").json()

    assert body["total_stocks"] >= 1
    assert body["market_observations"] > 0
    assert body["daily_snapshots"] > 0
    assert body["oldest_observation"] < body["latest_observation"]
    assert body["window"]["years"] == 2
    for key in ("queued", "processing", "completed", "partial", "failed"):
        assert key in body


def test_admin_anomalies_endpoint_answers(api):
    body = api.get("/admin/backfill/anomalies").json()
    assert "anomalies" in body and isinstance(body["anomalies"], list)


def test_admin_endpoints_require_a_token_when_one_is_configured(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ADMIN_TOKEN", "s3cret")
    assert client.get("/api/v1/admin/backfill/status").status_code == 403
    ok = client.get(
        "/api/v1/admin/backfill/status", headers={"X-Admin-Token": "s3cret"}
    )
    assert ok.status_code == 200


# -- the brief's canonical chart path, for both asset classes ---------------


def test_instrument_chart_serves_a_stock_by_ticker(api, seeded_stock):
    """The brief names GET /instruments/{identifier}/chart, not a per-asset path."""
    body = api.get(
        f"/instruments/{seeded_stock.ticker}/chart", params={"range": "2y"}
    ).json()

    assert body["instrument_kind"] == "stock"
    assert body["range"] == "2y"
    assert body["series"], body
    assert body["source"] == "daily_market_snapshots"
    # The brief asks for `historical_coverage`; the older per-asset routes
    # already shipped `coverage`. Both must be present and identical.
    assert body["historical_coverage"] == body["coverage"]
    assert "insufficient_history" in body


def test_instrument_chart_resolves_an_isin(api, seeded_stock):
    if not seeded_stock.isin:
        pytest.skip("fixture stock has no ISIN")
    body = api.get(
        f"/instruments/{seeded_stock.isin}/chart", params={"range": "1y"}
    ).json()
    assert body["instrument"]["ticker"] == seeded_stock.ticker


def test_instrument_chart_matches_the_stock_route(api, seeded_stock):
    """One instrument must not have two disagreeing charts."""
    unified = api.get(
        f"/instruments/{seeded_stock.ticker}/chart", params={"range": "6m"}
    ).json()
    per_asset = api.get(
        f"/stocks/{seeded_stock.ticker}/chart", params={"range": "6m"}
    ).json()
    assert unified["series"] == per_asset["series"]
    assert unified["resolution"] == per_asset["resolution"]


def test_instrument_chart_can_drop_events_and_add_scores(api, seeded_stock):
    body = api.get(
        f"/instruments/{seeded_stock.ticker}/chart",
        params={"range": "1y", "include_events": "false", "include_scores": "true"},
    ).json()
    assert body["events"] == []
    assert isinstance(body["scores"], list)


def test_instrument_chart_serves_a_bond(api, session):
    """Bonds answer from their own stored snapshots, in the same envelope."""
    from app.models.bond import Bond

    bond = session.query(Bond).first()
    if bond is None:
        pytest.skip("no bond in the test database")
    body = api.get(f"/instruments/{bond.ticker}/chart", params={"range": "1y"}).json()

    assert body["instrument_kind"] == "bond"
    assert body["instrument"]["ticker"] == bond.ticker
    assert body["resolution"] == "1d"
    assert isinstance(body["series"], list)
    assert body["historical_coverage"] == body["coverage"]


def test_instrument_chart_404s_on_an_unknown_identifier(api):
    assert api.get("/instruments/NOSUCHTHING/chart").status_code == 404


@pytest.mark.parametrize(
    "range_key", ["1d", "5d", "1m", "3m", "6m", "1y", "2y", "3y", "5y", "max"]
)
def test_instrument_chart_supports_every_briefed_range(api, seeded_stock, range_key):
    body = api.get(
        f"/instruments/{seeded_stock.ticker}/chart", params={"range": range_key}
    ).json()
    assert body["range"] == range_key
