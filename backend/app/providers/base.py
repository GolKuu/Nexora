"""The BondDataProvider contract.

Every provider returns DTOs that carry their own provenance. A provider that
cannot answer raises ``UpstreamError`` or returns an empty result - it never
substitutes plausible-looking numbers.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date, datetime

from app.core.enums import DataMode


@dataclass(slots=True)
class Provenance:
    source: str
    source_identifier: str | None = None
    source_url: str | None = None
    source_timestamp: datetime | None = None
    fetched_at: datetime | None = None
    data_mode: str = DataMode.CACHED.value


@dataclass(slots=True)
class ProviderIssuer:
    code: str
    name: str
    short_name: str | None = None
    bin: str | None = None
    country: str = "KZ"
    sector: str | None = None
    industry: str | None = None
    is_financial_institution: bool = False
    is_state_owned: bool = False
    website: str | None = None
    kase_url: str | None = None
    description: str | None = None
    provenance: Provenance | None = None


@dataclass(slots=True)
class ProviderBond:
    ticker: str
    name: str
    issuer_code: str
    isin: str | None = None
    currency: str = "KZT"
    nominal: float | None = None
    issue_date: date | None = None
    maturity_date: date | None = None
    coupon_rate: float | None = None
    coupon_type: str | None = None
    coupon_frequency: int | None = None
    next_coupon_date: date | None = None
    day_count: str = "ACT/365F"
    issue_size: float | None = None
    outstanding_amount: float | None = None
    market_segment: str | None = None
    bond_type: str | None = None
    secured: bool | None = None
    subordinated: bool | None = None
    callable: bool | None = None
    putable: bool | None = None
    guarantee: str | None = None
    kase_url: str | None = None
    is_active: bool = True
    provenance: Provenance | None = None


@dataclass(slots=True)
class ProviderQuote:
    ticker: str
    timestamp: datetime
    bid: float | None = None
    ask: float | None = None
    bid_volume: float | None = None
    ask_volume: float | None = None
    last: float | None = None
    clean_price: float | None = None
    dirty_price: float | None = None
    accrued_interest: float | None = None
    ytm: float | None = None
    volume: float | None = None
    turnover: float | None = None
    number_of_trades: int | None = None
    provenance: Provenance | None = None


@dataclass(slots=True)
class ProviderTrade:
    ticker: str
    timestamp: datetime
    trade_id: str | None = None
    price: float | None = None
    clean_price: float | None = None
    ytm: float | None = None
    quantity: float | None = None
    amount: float | None = None
    currency: str | None = None
    provenance: Provenance | None = None


@dataclass(slots=True)
class ProviderFinancials:
    issuer_code: str
    period_end: date
    period_type: str = "FY"
    currency: str = "KZT"
    values: dict[str, float | None] = field(default_factory=dict)
    is_audited: bool | None = None
    is_consolidated: bool | None = None
    standard: str | None = None
    provenance: Provenance | None = None


@dataclass(slots=True)
class ProviderRating:
    issuer_code: str
    agency: str
    rating: str
    scale: str | None = None
    outlook: str | None = None
    rating_date: date | None = None
    provenance: Provenance | None = None


@dataclass(slots=True)
class ProviderDocument:
    issuer_code: str
    title: str
    url: str
    kind: str | None = None
    published_at: date | None = None
    provenance: Provenance | None = None


@dataclass(slots=True)
class ProviderStatus:
    """Result of an honest connectivity probe - no assumptions, only checks."""

    name: str
    reachable: bool
    data_mode: str
    checked_at: datetime
    latency_ms: float | None = None
    detail: str | None = None
    is_mock: bool = False


class BondDataProvider(abc.ABC):
    """Interface every KASE data source implements."""

    name: str = "abstract"
    #: What the data this provider returns should be labelled as.
    data_mode: str = DataMode.CACHED.value
    #: True only for providers that fabricate data for demos.
    is_mock: bool = False

    @abc.abstractmethod
    async def get_bonds(self) -> list[ProviderBond]: ...

    @abc.abstractmethod
    async def get_bond(self, identifier: str) -> ProviderBond | None: ...

    @abc.abstractmethod
    async def search_bonds(self, query: str) -> list[ProviderBond]: ...

    @abc.abstractmethod
    async def get_quotes(self, tickers: list[str] | None = None) -> list[ProviderQuote]: ...

    @abc.abstractmethod
    async def get_trades(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[ProviderTrade]: ...

    @abc.abstractmethod
    async def get_issuer(self, identifier: str) -> ProviderIssuer | None: ...

    @abc.abstractmethod
    async def get_financials(self, issuer_code: str) -> list[ProviderFinancials]: ...

    @abc.abstractmethod
    async def get_documents(self, issuer_code: str) -> list[ProviderDocument]: ...

    @abc.abstractmethod
    async def get_ratings(self, issuer_code: str) -> list[ProviderRating]: ...

    async def health(self) -> ProviderStatus:  # pragma: no cover - overridden
        raise NotImplementedError

    async def aclose(self) -> None:
        return None
