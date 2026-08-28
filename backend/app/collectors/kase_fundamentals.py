"""Issuer fundamentals as KASE publishes them, and nothing beyond that.

Two KASE surfaces carry this:

``/api/companies/fin-data/{org_code}/``
    The reporting table behind every issuer page: revenue, gross income, net
    profit, equity, assets and liabilities, cumulative from the start of the
    fiscal year. Each row states its own scale and currency - most are in
    thousands of tenge, some in millions, and an issuer that reports in dollars
    says so in words - so a row whose scale or currency cannot be read is
    dropped rather than assumed. ``change_date`` is the day the period is
    reported *as of*, so a row dated 1 January closes the year before it.

``/en/investors/shares/{ticker}/``
    Rendered server-side, so the issue parameters - including the number of
    shares outstanding KASE itself confirms from issuer documents - are in the
    HTML rather than behind a separate endpoint. There is no API for it; this
    reads the same table a person would read on the page.

What KASE does not publish, this does not produce. There is no operating
profit, no cash balance, no borrowings split and no capital expenditure in
``fin-data``, so those columns stay NULL and every model that needs them keeps
reporting itself unavailable. Gross income is not operating profit and is never
stored as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.financials import FinancialStatement
from app.models.instrument import Instrument, SHARE_INSTRUMENT_TYPES
from app.models.issuer import Issuer
from app.models.stock import Stock

logger = get_logger(__name__)

SOURCE = "kase_public_api"
BASE_URL = "https://kase.kz"
FIN_DATA_PATH = "/api/companies/fin-data/{code}/"
SHARE_PAGE_PATH = "/en/investors/shares/{ticker}/"
#: KASE reports the table as of the first day after the period it closes.
PERIOD_TYPES = {1: "FY", 4: "Q1", 7: "H1", 10: "Q3"}
#: KASE states the scale per row and uses both of these. A row whose units are
#: not listed here is dropped: a figure read at the wrong scale is off by three
#: orders of magnitude, which is worse than having no figure.
UNIT_SCALE = {"thnd": 1e3, "thousand": 1e3, "ths": 1e3, "mln": 1e6, "mn": 1e6, "bln": 1e9}
#: The currency arrives as a Russian word rather than a code for issuers that
#: do not report in tenge - Freedom Holding reports in dollars. Reading those
#: figures as tenge would misprice the issuer by a factor of ~500.
CURRENCY_WORDS = {
    "тенге": "KZT", "теңге": "KZT", "долларов": "USD", "доллар": "USD",
    "долларов сша": "USD", "евро": "EUR", "рублей": "RUB", "рубль": "RUB",
}
SHARES_LABEL = re.compile(
    r'"label"\s*:\s*"Number of shares outstanding"\s*,\s*"value"\s*:\s*"([\d\s ,.]+)"'
)


@dataclass(frozen=True)
class ReportedPeriod:
    """One published row, already converted out of KASE's reporting units."""

    period_end: date
    period_type: str
    fiscal_year: int
    currency: str
    is_audited: bool | None
    revenue: float | None
    net_profit: float | None
    total_equity: float | None
    total_assets: float | None
    total_liabilities: float | None


def _scale(units: str | None) -> float | None:
    return UNIT_SCALE.get((units or "").strip().lower())


def _currency(value: str | None) -> str | None:
    raw = (value or "").strip()
    if len(raw) == 3 and raw.isascii() and raw.isalpha():
        return raw.upper()
    return CURRENCY_WORDS.get(raw.lower())


def _amount(value, scale: float) -> float | None:
    if value is None:
        return None
    try:
        return float(value) * scale
    except (TypeError, ValueError):
        return None


def parse_fin_data(payload: list[dict]) -> list[ReportedPeriod]:
    """Turn KASE's reporting rows into periods, dropping what it cannot date.

    KASE sometimes carries the same period twice - a restatement published over
    an earlier filing. The payload runs newest first, so the first row wins and
    the superseded one is dropped: one period is one row, never two.
    """
    seen: set[tuple[date, str]] = set()
    periods: list[ReportedPeriod] = []
    for row in payload or []:
        if not isinstance(row, dict):
            continue
        raw_date = row.get("change_date")
        if not raw_date:
            continue
        try:
            reported_as_of = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        period_type = PERIOD_TYPES.get(reported_as_of.month)
        if period_type is None or reported_as_of.day != 1:
            # A row KASE dates mid-month closes no period we can name, and
            # guessing which one it means would invent a reporting date.
            continue
        period_end = reported_as_of - timedelta(days=1)
        if (period_end, period_type) in seen:
            continue
        scale = _scale(row.get("units"))
        currency = _currency(row.get("currency"))
        if scale is None or currency is None:
            logger.warning("unreadable reporting row period=%s units=%r currency=%r",
                           period_end, row.get("units"), row.get("currency"))
            continue
        seen.add((period_end, period_type))
        periods.append(ReportedPeriod(
            period_end=period_end,
            period_type=period_type,
            fiscal_year=period_end.year,
            currency=currency,
            is_audited=row.get("audited"),
            revenue=_amount(row.get("volume_sale"), scale),
            net_profit=_amount(row.get("net_profit"), scale),
            total_equity=_amount(row.get("own_capital"), scale),
            total_assets=_amount(row.get("aggregate_assets"), scale),
            total_liabilities=_amount(row.get("total_liabilities"), scale),
        ))
    return sorted(periods, key=lambda item: item.period_end)


def parse_shares_outstanding(html: str) -> float | None:
    match = SHARES_LABEL.search(html or "")
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(1))
    if not digits:
        return None
    value = float(digits)
    return value if value > 0 else None


class KaseFundamentalsClient:
    """Thin synchronous reader for the two published surfaces."""

    def __init__(self, *, base_url: str = BASE_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"Accept": "application/json, text/html", "User-Agent": "Nexora/0.1 (KASE reader)"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "KaseFundamentalsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fin_data(self, org_code: str) -> list[ReportedPeriod]:
        url = self.base_url + FIN_DATA_PATH.format(code=org_code)
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("fin-data unreachable code=%s error=%s", org_code, exc)
            return []
        if response.status_code != 200:
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        return parse_fin_data(payload if isinstance(payload, list) else [])

    def shares_outstanding(self, ticker: str) -> float | None:
        url = self.base_url + SHARE_PAGE_PATH.format(ticker=ticker)
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("share page unreachable ticker=%s error=%s", ticker, exc)
            return None
        if response.status_code != 200:
            return None
        return parse_shares_outstanding(response.text)

    def source_url(self, org_code: str) -> str:
        return f"{self.base_url}/en/issuers/show/{org_code}/"


def _apply(statement: FinancialStatement, period: ReportedPeriod, url: str, now: datetime) -> bool:
    """Write the published lines onto a statement; report whether it changed."""
    values = {
        "fiscal_year": period.fiscal_year,
        "currency": period.currency,
        "is_audited": period.is_audited,
        "revenue": period.revenue,
        "net_profit": period.net_profit,
        "total_equity": period.total_equity,
        "total_assets": period.total_assets,
        "total_liabilities": period.total_liabilities,
    }
    changed = False
    for field, value in values.items():
        if getattr(statement, field) != value:
            setattr(statement, field, value)
            changed = True
    statement.source = SOURCE
    statement.source_url = url
    statement.source_identifier = f"fin-data:{period.period_end.isoformat()}"
    statement.fetched_at = now
    if statement.source_timestamp is None:
        statement.source_timestamp = now
    return changed


def import_fundamentals(
    session: Session,
    *,
    tickers: list[str] | None = None,
    client: KaseFundamentalsClient | None = None,
    dry_run: bool = False,
) -> dict:
    """Refresh statements and share counts for every listed share we track."""
    owns_client = client is None
    client = client or KaseFundamentalsClient()
    wanted = {value.upper() for value in tickers} if tickers else None
    savepoint = session.begin_nested() if dry_run else None
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(Instrument, Stock, Issuer)
        .join(Stock, Stock.instrument_id == Instrument.id)
        .join(Issuer, Issuer.id == Instrument.issuer_id)
        .where(Instrument.instrument_type.in_(SHARE_INSTRUMENT_TYPES))
    ).all()

    statements_created = statements_updated = statements_removed = 0
    shares_set = shares_missing = 0
    issuers_without_data: list[str] = []
    done_issuers: set[int] = set()
    try:
        for instrument, stock, issuer in rows:
            if wanted is not None and instrument.ticker.upper() not in wanted:
                continue

            shares = client.shares_outstanding(instrument.ticker)
            if shares is None:
                shares_missing += 1
            elif stock.shares_outstanding != shares:
                stock.shares_outstanding = shares
                stock.last_checked_at = now
                stock.last_changed_at = now
                shares_set += 1

            if issuer.id in done_issuers:
                continue
            done_issuers.add(issuer.id)
            periods = client.fin_data(issuer.code)
            if not periods:
                issuers_without_data.append(issuer.code)
                continue
            url = client.source_url(issuer.code)
            existing = {
                (row.period_end, row.period_type): row
                for row in session.execute(
                    select(FinancialStatement).where(FinancialStatement.issuer_id == issuer.id)
                ).scalars()
            }
            # Rows this collector wrote from a publication it can no longer
            # read the same way - a scale or currency it now refuses - are
            # removed. Leaving them would keep a figure the source no longer
            # supports. Only this collector's own rows are ever dropped.
            published = {(item.period_end, item.period_type) for item in periods}
            for key, statement in list(existing.items()):
                if key in published or not (statement.source_identifier or "").startswith("fin-data:"):
                    continue
                session.delete(statement)
                existing.pop(key, None)
                statements_removed += 1

            for period in periods:
                statement = existing.get((period.period_end, period.period_type))
                if statement is None:
                    statement = FinancialStatement(
                        issuer_id=issuer.id, period_end=period.period_end,
                        period_type=period.period_type, source_timestamp=now,
                    )
                    session.add(statement)
                    existing[(period.period_end, period.period_type)] = statement
                    _apply(statement, period, url, now)
                    statements_created += 1
                elif statement.source in (None, SOURCE, "mock") and _apply(statement, period, url, now):
                    statements_updated += 1
    finally:
        if owns_client:
            client.close()

    session.flush()
    if dry_run:
        assert savepoint is not None
        savepoint.rollback()
        session.expire_all()
    else:
        session.commit()
    return {"instruments": len(rows), "statements_created": statements_created,
            "statements_updated": statements_updated, "statements_removed": statements_removed, "shares_outstanding_set": shares_set,
            "shares_outstanding_missing": shares_missing,
            "issuers_without_fin_data": len(issuers_without_data),
            "issuers_without_fin_data_codes": issuers_without_data, "dry_run": dry_run}


__all__ = ["KaseFundamentalsClient", "ReportedPeriod", "import_fundamentals",
           "parse_fin_data", "parse_shares_outstanding"]
