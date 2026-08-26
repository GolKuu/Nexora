"""Daily OHLCV history from the public chart feed kase.kz serves to its own pages.

Every instrument page on kase.kz renders its price chart from
``/tv-charts/securities/*`` — unauthenticated public endpoints the visitor's own
browser calls while the page loads. Reading them directly is the
"structured public data first" branch of the collection policy in
:mod:`app.services.backfill.collector`: one request returns the exchange's own
OHLCV series for the whole window, instead of paginating a rendered table and
parsing text back into numbers.

The policy limits still hold, and are the reason this module exists separately
from the browser crawl: no authentication, no private endpoints, one polite
request per instrument. A day the feed does not report stays absent — a gap in
the series is recorded as a gap, never interpolated.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.core.config import settings
from app.core.logging import get_logger
from app.forecast.calendar import KASE_TZ
from app.providers.http import HttpFetcher
from app.services.backfill.records import STATUS_TRADED, ObservationRecord
from app.services.backfill.window import BackfillWindow

logger = get_logger(__name__)

SOURCE_NAME = "kase_public_chart_api"
PARSER_VERSION = "kase-udf-history-v1"

#: The chart feed only publishes daily bars and coarser aggregates.
DAILY_RESOLUTION = "D"


def history_url(base_url: str | None = None) -> str:
    base = (base_url or settings.KASE_WEBSITE_URL).rstrip("/")
    return f"{base}/tv-charts/securities/history"


def _bar_timestamp(epoch_seconds: float) -> datetime:
    """A daily bar is stamped at the close of that trading day in Almaty."""
    day = datetime.fromtimestamp(float(epoch_seconds), tz=timezone.utc).date()
    return datetime.combine(day, datetime.min.time(), tzinfo=KASE_TZ).astimezone(timezone.utc)


def _number(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) else None


def parse_history(payload: dict, window: BackfillWindow, source_url: str) -> list[ObservationRecord]:
    """Turn one UDF response into observation records inside ``window``.

    ``s`` is the feed's own status field: ``no_data`` is a legitimate answer for
    an instrument that never traded in the window and yields no records rather
    than an error.
    """
    if not isinstance(payload, dict) or payload.get("s") != "ok":
        return []
    times = payload.get("t") or []
    closes = payload.get("c") or []
    opens = payload.get("o") or []
    highs = payload.get("h") or []
    lows = payload.get("l") or []
    volumes = payload.get("v") or []
    records: list[ObservationRecord] = []
    for index, epoch in enumerate(times):
        close = _number(closes[index] if index < len(closes) else None)
        if close is None:
            # A bar without a close carries no price fact worth storing.
            continue
        observed_at = _bar_timestamp(epoch)
        if not window.contains(observed_at):
            continue
        records.append(ObservationRecord(
            observed_at=observed_at,
            trading_date=observed_at.astimezone(KASE_TZ).date(),
            price=close,
            close=close,
            open=_number(opens[index] if index < len(opens) else None),
            high=_number(highs[index] if index < len(highs) else None),
            low=_number(lows[index] if index < len(lows) else None),
            volume=_number(volumes[index] if index < len(volumes) else None),
            status=STATUS_TRADED,
            source=SOURCE_NAME,
            source_url=source_url,
            parser_version=PARSER_VERSION,
            data_mode="public_api",
        ))
    return records


class KaseChartHistoryClient:
    """One polite request per instrument against the public chart feed."""

    def __init__(self, *, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.KASE_WEBSITE_URL).rstrip("/")
        self.timeout = settings.KASE_HTTP_TIMEOUT if timeout is None else timeout

    async def daily_history(self, ticker: str, window: BackfillWindow) -> list[ObservationRecord]:
        """Daily bars for ``ticker``; an empty list when the feed gives nothing."""
        url = history_url(self.base_url)
        params = {
            "symbol": ticker,
            "resolution": DAILY_RESOLUTION,
            "from": int(window.start.timestamp()),
            "to": int(window.end.timestamp()),
        }
        fetcher = HttpFetcher(
            self.base_url,
            timeout=self.timeout,
            # The feed is part of the instrument page; identify the page we came from.
            headers={"Accept": "application/json", "Referer": f"{self.base_url}/"},
        )
        try:
            response = await fetcher.fetch(url, params=params)
        finally:
            await fetcher.aclose()
        payload = response.json
        if payload is None and response.text:
            # The feed answers with JSON under a text/plain content type.
            try:
                payload = json.loads(response.text)
            except ValueError:
                payload = None
        if not response.ok or not isinstance(payload, dict):
            logger.info(
                "chart history unavailable ticker=%s status=%s error=%s",
                ticker, response.status, response.error,
            )
            return []
        records = parse_history(payload, window, response.url)
        logger.info("chart history ticker=%s bars=%s", ticker, len(records))
        return records


__all__ = [
    "DAILY_RESOLUTION",
    "KaseChartHistoryClient",
    "PARSER_VERSION",
    "SOURCE_NAME",
    "history_url",
    "parse_history",
]
