"""Parsed-but-not-yet-trusted historical records.

Collectors produce these; :mod:`app.services.backfill.validate` decides whether
they may become facts. Keeping the two apart is what makes "never fabricate
history" enforceable: a record that fails validation becomes an
:class:`~app.models.history.IngestionAnomaly`, not a row in the price series.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime

#: Why a point in the series exists (or does not).
STATUS_TRADED = "traded"
STATUS_NO_TRADE = "no_trade"
STATUS_MARKET_CLOSED = "market_closed"
STATUS_DATA_UNAVAILABLE = "data_unavailable"

ALL_STATUSES = (STATUS_TRADED, STATUS_NO_TRADE, STATUS_MARKET_CLOSED, STATUS_DATA_UNAVAILABLE)


def _digest(parts: list) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _round(value: float | None, places: int = 6) -> float | None:
    return None if value is None else round(float(value), places)


@dataclass(slots=True)
class ObservationRecord:
    """One market reading, as parsed from a public KASE page."""

    observed_at: datetime
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_volume: float | None = None
    ask_volume: float | None = None
    volume: float | None = None
    turnover: float | None = None
    trade_count: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    previous_close: float | None = None
    status: str = STATUS_TRADED
    source: str | None = None
    source_url: str | None = None
    source_timestamp: datetime | None = None
    parser_version: str | None = None
    data_mode: str = "browser"
    trading_date: date | None = None

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def has_market_data(self) -> bool:
        return any(
            v is not None
            for v in (self.price, self.close, self.bid, self.ask, self.volume, self.turnover)
        )

    def fingerprint(self) -> str:
        """Stable across re-runs, sensitive to any change in the values.

        Two backfills of the same day produce the same fingerprint and therefore
        the same row; a corrected price produces a different one and is stored
        as a new observation rather than overwriting the original.
        """
        return _digest([
            self.observed_at.isoformat(),
            self.status,
            _round(self.price), _round(self.bid), _round(self.ask),
            _round(self.volume), _round(self.turnover),
            self.trade_count,
            _round(self.open), _round(self.high), _round(self.low), _round(self.close),
        ])


@dataclass(slots=True)
class TradeRecord:
    """One historical deal."""

    trade_timestamp: datetime
    price: float
    quantity: float | None = None
    trade_value: float | None = None
    trade_id: str | None = None
    source: str | None = None
    source_url: str | None = None
    source_timestamp: datetime | None = None
    parser_version: str | None = None
    data_mode: str = "browser"

    def fingerprint(self) -> str:
        """The official trade id when KASE gives one, a content hash otherwise."""
        if self.trade_id:
            return _digest(["trade_id", self.trade_id])
        return _digest([
            "synthetic_key",
            self.trade_timestamp.isoformat(),
            _round(self.price),
            _round(self.quantity),
            _round(self.trade_value),
        ])


@dataclass(slots=True)
class DividendRecord:
    amount_per_share: float | None = None
    currency: str = "KZT"
    announcement_date: date | None = None
    ex_date: date | None = None
    record_date: date | None = None
    payment_date: date | None = None
    status: str = "announced"
    source: str | None = None
    source_url: str | None = None
    parser_version: str | None = None

    def fingerprint(self) -> str:
        return _digest([
            self.ex_date, self.record_date, self.payment_date,
            _round(self.amount_per_share), self.currency,
        ])


@dataclass(slots=True)
class ReportRecord:
    reporting_period: date
    document_hash: str
    period_type: str | None = None
    publication_date: date | None = None
    available_at: datetime | None = None
    document_url: str | None = None
    title: str | None = None
    source: str | None = None
    parser_version: str | None = None


@dataclass(slots=True)
class NewsRecord:
    """One issuer publication linked from a public KASE page.

    Only what the page actually stated: the headline text, its own URL and the
    date printed next to it. A publication whose date the page never showed
    keeps ``publication_date=None`` rather than being dated by the crawl.
    """

    title: str
    url: str
    publication_date: datetime | None = None
    issuer_code: str | None = None
    event_type: str | None = None
    excerpt: str | None = None
    source: str | None = None
    section: str | None = None
    parser_version: str | None = None

    def content_hash(self) -> str:
        return _digest([self.title.casefold(), self.url, self.publication_date])

    def as_item(self) -> dict:
        """The shape the existing news/corporate-action ingestion expects.

        ``content`` carries the headline plus whatever excerpt the listing
        showed, which is what lets a dividend announcement be recognised - and
        an announcement with no stated amount stays a news item rather than
        becoming a fabricated dividend.
        """
        return {
            "title": self.title,
            "url": self.url,
            "publication_date": self.publication_date,
            "content": " ".join(filter(None, (self.title, self.excerpt))),
            "event_type": self.event_type,
            "source": self.source,
            "section": self.section,
        }


@dataclass(slots=True)
class CollectionResult:
    """Everything one collector managed to read from one public page."""

    observations: list[ObservationRecord] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    dividends: list[DividendRecord] = field(default_factory=list)
    reports: list[ReportRecord] = field(default_factory=list)
    news: list[NewsRecord] = field(default_factory=list)
    #: Pages visited, for pacing accounting and for the audit trail.
    pages_visited: int = 0
    source_urls: list[str] = field(default_factory=list)
    #: Set when the site refused us (CAPTCHA, block page). The crawl stops.
    blocked: bool = False
    notes: list[str] = field(default_factory=list)

    def extend(self, other: "CollectionResult") -> "CollectionResult":
        self.observations.extend(other.observations)
        self.trades.extend(other.trades)
        self.dividends.extend(other.dividends)
        self.reports.extend(other.reports)
        self.news.extend(other.news)
        self.pages_visited += other.pages_visited
        self.source_urls.extend(other.source_urls)
        self.blocked = self.blocked or other.blocked
        self.notes.extend(other.notes)
        return self

    @property
    def is_empty(self) -> bool:
        return not (
            self.observations or self.trades or self.dividends
            or self.reports or self.news
        )


__all__ = [
    "ALL_STATUSES",
    "CollectionResult",
    "DividendRecord",
    "NewsRecord",
    "ObservationRecord",
    "ReportRecord",
    "STATUS_DATA_UNAVAILABLE",
    "STATUS_MARKET_CLOSED",
    "STATUS_NO_TRADE",
    "STATUS_TRADED",
    "TradeRecord",
]
