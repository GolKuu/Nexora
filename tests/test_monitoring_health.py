"""Monitoring must be able to prove it is running (§41).

The health answer is built from ``monitoring_cycles`` rows the loop writes
itself, so these tests never assert on configuration - they run real cycles and
then read what the cycles recorded.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.history import MonitoringCycle
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock, StockQuote
from app.services.health_service import monitoring_health
from app.services.monitoring import MonitoringService


@pytest.fixture
def monitored(session) -> Instrument:
    unique = uuid.uuid4().hex[:8].upper()
    issuer = Issuer(name="Monitored Issuer", code=f"MONISS{unique}", sector="corporate")
    session.add(issuer)
    session.flush()
    instrument = Instrument(
        ticker=f"MON{unique}",
        isin=f"KZ{unique}MON",
        issuer_id=issuer.id,
        instrument_type="stock",
        currency="KZT",
        is_active=True,
    )
    session.add(instrument)
    session.flush()
    session.add(Stock(instrument_id=instrument.id))
    session.flush()
    return instrument


def _quote(session, instrument: Instrument, price: float, moment: datetime) -> None:
    stock = session.query(Stock).filter_by(instrument_id=instrument.id).one()
    session.add(StockQuote(
        stock_id=stock.id, timestamp=moment, last=price,
        data_mode="browser", source="kase_public_website",
    ))
    session.flush()


@pytest.mark.anyio
async def test_a_cycle_records_itself(session, monitored, anyio_backend):
    moment = datetime.now(timezone.utc).replace(microsecond=0)
    _quote(session, monitored, 1010.0, moment)

    result = await MonitoringService(session).observe_active()

    cycle = session.get(MonitoringCycle, result["cycle_id"])
    assert cycle is not None
    assert cycle.job_type == "monitoring"
    assert cycle.status == "ok"
    assert cycle.instruments_checked >= 1
    assert cycle.instruments_changed >= 1, "a new reading is a change"
    assert cycle.failures == 0
    assert cycle.finished_at >= cycle.started_at
    assert cycle.duration_ms >= 0
    assert cycle.interval_seconds == 600


@pytest.mark.anyio
async def test_an_unchanged_pass_is_healthy_and_writes_no_observation(
    session, monitored, anyio_backend
):
    """§9: re-reading the same price stores nothing, and that is not a failure."""
    moment = datetime.now(timezone.utc).replace(microsecond=0)
    _quote(session, monitored, 1010.0, moment)
    service = MonitoringService(session)

    first = await service.observe_active()
    second = await service.observe_active()

    assert first["observations_created"] >= 1
    assert second["observations_created"] == 0
    assert second["instruments_changed"] == 0
    assert second["status"] == "ok", "a quiet cycle is a healthy cycle"

    health = monitoring_health(session)
    assert health["status"] == "ok"
    assert health["instruments_changed"] == 0
    assert health["failures"] == 0


@pytest.mark.anyio
async def test_health_reports_the_last_cycle_and_predicts_the_next(
    session, monitored, anyio_backend
):
    moment = datetime.now(timezone.utc).replace(microsecond=0)
    _quote(session, monitored, 1010.0, moment)
    await MonitoringService(session).observe_active()

    health = monitoring_health(session)

    assert health["status"] == "ok"
    assert health["server_side"] is True
    assert health["interval_seconds"] == 600
    assert health["schedule"] == "*/10 * * * *"
    assert health["last_cycle_at"] and health["last_successful_cycle_at"]
    assert health["cycles_observed"] >= 1
    assert health["average_latency_ms"] is not None
    # The next pass is one interval after the last one finished.
    last = datetime.fromisoformat(health["last_cycle_at"])
    following = datetime.fromisoformat(health["next_cycle_at"])
    assert following - last == timedelta(seconds=health["interval_seconds"])
    # A ten-minute public-web refresh is never labelled real-time (§40).
    assert health["data_mode"] == "delayed"


def test_a_loop_that_never_ran_is_not_reported_as_healthy(session):
    session.query(MonitoringCycle).delete()
    session.flush()

    assert monitoring_health(session)["status"] == "never_run"


def test_a_stopped_loop_is_reported_as_stalled(session):
    session.query(MonitoringCycle).delete()
    long_ago = datetime.now(timezone.utc) - timedelta(hours=6)
    session.add(MonitoringCycle(
        job_type="monitoring", started_at=long_ago, finished_at=long_ago,
        duration_ms=120, instruments_checked=4, instruments_changed=0,
        observations_created=0, duplicates=4, failures=0, anomalies=0,
        status="ok", market_day=True, interval_seconds=600,
    ))
    session.flush()

    health = monitoring_health(session)
    assert health["status"] == "stalled", "configuration alone is not health"
    assert health["seconds_since_last_cycle"] > 600 * 3


def test_a_failing_instrument_degrades_the_cycle_without_stopping_it(session):
    session.query(MonitoringCycle).delete()
    now = datetime.now(timezone.utc)
    session.add(MonitoringCycle(
        job_type="monitoring", started_at=now, finished_at=now,
        duration_ms=900, instruments_checked=12, instruments_changed=3,
        observations_created=3, duplicates=8, failures=1, anomalies=2,
        status="degraded", market_day=True, interval_seconds=600,
        error="timeout",
    ))
    session.flush()

    health = monitoring_health(session)
    assert health["status"] == "degraded"
    assert health["instruments_checked"] == 12
    assert health["failures"] == 1
    assert health["parser_anomalies"] == 2
    assert health["last_error"] == "timeout"


def test_health_endpoint_is_public_and_answers(api):
    body = api.get("/health/monitoring")
    assert body.status_code == 200
    payload = body.json()
    assert payload["status"] in {"ok", "degraded", "stalled", "never_run"}
    for field in (
        "interval_seconds", "next_cycle_at", "instruments_checked",
        "instruments_changed", "failures", "parser_anomalies",
        "average_latency_ms", "last_successful_cycle_at",
    ):
        assert field in payload, field
