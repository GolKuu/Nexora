"""Daily closing prices for every KASE share, from what KASE itself publishes.

The exchange's free API exposes no history endpoint - ``/api/instruments/
securities/`` answers with the current session plus ``monthly_spark_line``, a
semicolon-separated run of the last consecutive session closes for that
security. Those values are real published data, so they are history the product
may keep; they simply arrive undated, because KASE ships the numbers alone.

The dates are reconstructed, never invented: the last value belongs to the
session the payload itself is stamped with (``date0``), and the earlier ones
walk backwards over the KASE trading calendar. The same payload carries the
check - ``trand`` is the change against the previous close, and the second to
last spark value must reproduce it - so a series that disagrees is rejected
rather than stored.

What this cannot do is reach further back than KASE publishes. Roughly a month
of sessions is what the sparkline holds; the deeper archive is the licensed
deals register, which :mod:`app.collectors.kase_history_importer` loads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import hashlib
import json

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.forecast.calendar import KASE_TZ, kase_date, previous_trading_days
from app.models.instrument import Instrument, SHARE_INSTRUMENT_TYPES
from app.models.stock import Stock, StockQuote
from app.services.monitoring import MonitoringService

logger = get_logger(__name__)

SOURCE = "kase_public_api"
CATALOG_PATH = "/api/instruments/securities/"
#: The close is the value of a finished session, so it is stamped at the end of
#: that session rather than at an invented intraday moment.
SESSION_CLOSE = time(17, 0)
#: How far the reconstructed close may sit from what ``trand`` implies before
#: the whole series is treated as misaligned. KASE rounds the printed change.
TREND_TOLERANCE = 0.01


@dataclass(frozen=True)
class DailyClose:
    ticker: str
    trading_date: date
    close: float

    @property
    def observed_at(self) -> datetime:
        return datetime.combine(self.trading_date, SESSION_CLOSE, tzinfo=KASE_TZ)

    @property
    def content_hash(self) -> str:
        payload = {"ticker": self.ticker, "date": self.trading_date.isoformat(), "close": self.close}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


def _spark_values(raw: str | None) -> list[float]:
    if not raw:
        return []
    values: list[float] = []
    for chunk in str(raw).split(";"):
        chunk = chunk.strip().replace(" ", "").replace(" ", "").replace(",", ".")
        if not chunk:
            continue
        try:
            value = float(chunk)
        except ValueError:
            return []
        if value <= 0:
            return []
        values.append(value)
    return values


def _session_date(row: dict) -> date | None:
    stamp = row.get("date0")
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KASE_TZ)
    return kase_date(parsed)


def _trend_agrees(values: list[float], row: dict) -> bool:
    """Does the series line up with the change KASE printed for the session?"""
    trend = row.get("trand")
    if trend is None or len(values) < 2:
        return True
    try:
        printed = float(trend)
    except (TypeError, ValueError):
        return True
    implied = values[-1] - values[-2]
    return abs(implied - printed) <= max(TREND_TOLERANCE, abs(printed) * 0.01)


def daily_closes(row: dict) -> list[DailyClose]:
    """Dated closes for one catalog row, or ``[]`` when they cannot be trusted."""
    ticker = str(row.get("code") or "").strip()
    values = _spark_values(row.get("monthly_spark_line"))
    last_session = _session_date(row)
    if not ticker or not values or last_session is None:
        return []
    if not _trend_agrees(values, row):
        logger.warning("spark line disagrees with printed change ticker=%s", ticker)
        return []
    anchor = datetime.combine(last_session, SESSION_CLOSE, tzinfo=KASE_TZ)
    earlier = previous_trading_days(anchor, len(values) - 1)
    days = [last_session] + [kase_date(moment) for moment in earlier]
    dated = [DailyClose(ticker, day, value) for day, value in zip(days, reversed(values))]
    return list(reversed(dated))


def fetch_share_catalog(*, timeout: float = 30.0, base_url: str = "https://kase.kz") -> list[dict]:
    """Every listed share in one request, current session included."""
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"Accept": "application/json"}) as client:
        response = client.get(f"{base_url}{CATALOG_PATH}", params={"sec_type": "share", "size": 1000})
        response.raise_for_status()
        payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("results", [])
    return [row for row in rows if isinstance(row, dict)]


def import_daily_closes(
    session: Session,
    rows: list[dict],
    *,
    tickers: list[str] | None = None,
    since: date | None = None,
    dry_run: bool = False,
) -> dict:
    """Store every published close we do not already hold, then promote it.

    Readings already taken during a session are kept as they were - history is
    append-only here, so the official close joins them rather than replacing
    them. Because it is stamped at the end of the session it is the last
    observation of its day, which is what the daily snapshot closes on.

    A close is skipped when the same value for the same day is already stored,
    and when its session has not finished yet: a close that has not happened is
    not a fact.
    """
    wanted = {value.upper() for value in tickers} if tickers else None
    stocks = {
        instrument.ticker.upper(): (instrument, stock)
        for instrument, stock in session.execute(
            select(Instrument, Stock)
            .join(Stock, Stock.instrument_id == Instrument.id)
            .where(Instrument.instrument_type.in_(SHARE_INSTRUMENT_TYPES))
        ).all()
    }
    savepoint = session.begin_nested() if dry_run else None
    now = datetime.now(KASE_TZ)
    created = skipped = unknown = 0
    touched: dict[str, tuple[Instrument, Stock]] = {}
    for row in rows:
        code = str(row.get("code") or "").strip().upper()
        if wanted is not None and code not in wanted:
            continue
        pair = stocks.get(code)
        if pair is None:
            unknown += 1
            continue
        instrument, stock = pair
        closes = [item for item in daily_closes(row) if since is None or item.trading_date >= since]
        if not closes:
            continue
        held = {
            digest
            for (digest,) in session.execute(
                select(StockQuote.content_hash).where(StockQuote.stock_id == stock.id)
            ).all()
            if digest
        }
        for item in closes:
            if item.content_hash in held or item.observed_at > now:
                skipped += 1
                continue
            session.add(StockQuote(
                stock_id=stock.id, timestamp=item.observed_at, close=item.close, last=item.close,
                data_mode="historical", source=SOURCE,
                source_url=f"https://kase.kz/en/shares/show/{instrument.ticker}/",
                source_identifier=f"monthly_spark_line:{item.trading_date.isoformat()}",
                source_timestamp=item.observed_at, fetched_at=now, content_hash=item.content_hash,
            ))
            held.add(item.content_hash)
            created += 1
            touched[code] = (instrument, stock)
    session.flush()

    monitoring = MonitoringService(session)
    observations = duplicates = 0
    for instrument, stock in touched.values():
        result = monitoring.promote_quotes(instrument, stock, limit=100_000)
        observations += result.get("created", 0)
        duplicates += result.get("duplicates", 0)

    if dry_run:
        assert savepoint is not None
        savepoint.rollback()
        session.expire_all()
    else:
        session.commit()
    return {"catalog_rows": len(rows), "quotes_created": created, "already_held": skipped,
            "not_listed_here": unknown, "stocks_touched": len(touched),
            "observations_created": observations, "observations_duplicate": duplicates,
            "dry_run": dry_run}


__all__ = ["DailyClose", "daily_closes", "fetch_share_catalog", "import_daily_closes"]
