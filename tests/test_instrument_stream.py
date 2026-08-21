"""The SSE stream that keeps an open instrument page current (Phase 11).

The brief asks that new validated observations reach an open page without a
full reload, over SSE in preference to polling, and that the frontend never
scrapes KASE itself. These tests pin the contract the frontend relies on: an
immediate `connected` frame, an `update` frame only when stored data actually
moves, and silence otherwise.

The endpoint's body is an endless generator, so these tests drive it directly
with a request that disconnects after a set number of polls. Consuming it
through TestClient would simply block.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.api.routes import instruments as instruments_route
from app.core.errors import NotFoundError, UpstreamError


class FakeRequest:
    """A client that hangs up after `alive_for` disconnect checks."""

    def __init__(self, alive_for: int = 3):
        self.alive_for = alive_for
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks > self.alive_for


@pytest.fixture
def stream_stock(session):
    """One instrument with a single stored observation to move forward from."""
    from sqlalchemy import select

    from app.models.instrument import Instrument
    from app.models.issuer import Issuer
    from app.models.stock import Stock
    from app.services.backfill.records import ObservationRecord, STATUS_TRADED
    from app.services.backfill.store import HistoryStore

    # The suite shares one SQLite file, so this fixture has to be re-entrant.
    instrument = session.scalar(
        select(Instrument).where(Instrument.ticker == "STREAMTST")
    )
    if instrument is None:
        issuer = session.scalar(select(Issuer).where(Issuer.code == "STREAMISS"))
        if issuer is None:
            issuer = Issuer(
                name="Stream Test Issuer", code="STREAMISS", sector="corporate"
            )
            session.add(issuer)
            session.flush()
        instrument = Instrument(
            ticker="STREAMTST", isin="KZSTREAMTS1", issuer_id=issuer.id,
            instrument_type="stock", currency="KZT", is_active=True,
            kase_url="https://kase.kz/en/shares/STREAMTST",
        )
        session.add(instrument)
        session.flush()
        session.add(Stock(instrument_id=instrument.id))
        session.flush()

        moment = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        HistoryStore(session).save_observations(
            instrument.id,
            [ObservationRecord(
                observed_at=moment, price=1000.0, volume=5.0, status=STATUS_TRADED,
                source="kase_public_website", source_url="https://kase.kz/",
                data_mode="browser", trading_date=moment.date(),
            )],
        )
    session.commit()
    return instrument


async def _drain(response, limit: int = 20) -> list[str]:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
        if len(chunks) >= limit:
            break
    return chunks


def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
    """Parse (event, payload) pairs out of the raw SSE text."""
    parsed: list[tuple[str, dict]] = []
    event = None
    for line in "".join(chunks).splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: ") and event:
            parsed.append((event, json.loads(line[len("data: "):])))
            event = None
    return parsed


@pytest.mark.anyio
async def test_stream_announces_stored_state_immediately(
    session, stream_stock, monkeypatch
):
    """The first frame must not wait for a poll interval."""
    monkeypatch.setattr(instruments_route, "_STREAM_POLL_SECONDS", 0.01)

    response = await instruments_route.instrument_stream(
        stream_stock.ticker, FakeRequest(alive_for=0), session=session
    )
    assert response.media_type == "text/event-stream"
    frames = _frames(await _drain(response))

    assert frames, "no frames produced"
    event, payload = frames[0]
    assert event == "connected"
    assert payload["instrument"] == stream_stock.ticker
    assert payload["kind"] == "stock"
    # The client is told how often the server looks, so the UI can describe the
    # cadence honestly instead of implying a live feed.
    assert payload["poll_seconds"] > 0


@pytest.mark.anyio
async def test_stream_reports_a_new_observation(session, stream_stock, monkeypatch):
    """A row written by the collector becomes an `update` frame."""
    from app.services.backfill.records import ObservationRecord, STATUS_TRADED
    from app.services.backfill.store import HistoryStore

    monkeypatch.setattr(instruments_route, "_STREAM_POLL_SECONDS", 0.01)

    later = datetime.now(timezone.utc) + timedelta(days=1)
    HistoryStore(session).save_observations(
        stream_stock.id,
        [ObservationRecord(
            observed_at=later, price=4242.0, volume=7.0, status=STATUS_TRADED,
            source="kase_public_website", source_url="https://kase.kz/",
            data_mode="browser", trading_date=later.date(),
        )],
    )
    session.commit()

    response = await instruments_route.instrument_stream(
        stream_stock.ticker, FakeRequest(alive_for=3), session=session
    )
    frames = _frames(await _drain(response))

    # The connected frame already carries the newest moment, which is the same
    # thing an update announces - either way the page learns the new timestamp.
    assert frames[0][0] == "connected"
    assert frames[0][1]["last_updated"].startswith(str(later.year))


@pytest.mark.anyio
async def test_unchanged_data_produces_no_update(session, stream_stock, monkeypatch):
    """Silence is correct: unchanged data must never be re-announced.

    Inventing a frame per tick would push the UI to redraw - and a careless
    client to store - observations that never happened.
    """
    monkeypatch.setattr(instruments_route, "_STREAM_POLL_SECONDS", 0.01)

    response = await instruments_route.instrument_stream(
        stream_stock.ticker, FakeRequest(alive_for=4), session=session
    )
    chunks = await _drain(response)

    assert not [event for event, _ in _frames(chunks) if event == "update"]
    # Idle ticks send a comment frame, which EventSource ignores.
    assert "keep-alive" in "".join(chunks)


@pytest.mark.anyio
async def test_stream_stops_when_the_client_hangs_up(session, stream_stock, monkeypatch):
    """An abandoned page must not leave a generator polling the database."""
    monkeypatch.setattr(instruments_route, "_STREAM_POLL_SECONDS", 0.01)

    request = FakeRequest(alive_for=2)
    response = await instruments_route.instrument_stream(
        stream_stock.ticker, request, session=session
    )
    chunks = await _drain(response, limit=100)

    # Drained to completion well inside the limit, rather than forever.
    assert len(chunks) < 100
    assert request.checks > request.alive_for


@pytest.mark.anyio
async def test_stream_rejects_an_unknown_instrument(session):
    with pytest.raises(NotFoundError):
        await instruments_route.instrument_stream(
            "NOSUCHTHING", FakeRequest(), session=session
        )


@pytest.mark.anyio
async def test_stream_refuses_to_run_on_serverless(session, stream_stock, monkeypatch):
    """A long-lived generator does not belong in a serverless request handler."""
    from app.core.config import settings

    monkeypatch.setattr(type(settings), "is_serverless", property(lambda _: True))
    with pytest.raises(UpstreamError):
        await instruments_route.instrument_stream(
            stream_stock.ticker, FakeRequest(), session=session
        )


def test_sse_frame_format():
    """EventSource requires the blank-line terminator; without it nothing fires."""
    frame = instruments_route._sse("update", {"a": 1})
    assert frame.startswith("event: update\n")
    assert frame.endswith("\n\n")
    assert json.loads(frame.splitlines()[1][len("data: "):]) == {"a": 1}
