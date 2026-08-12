"""Provider for an official KASE API.

Important: this project does not ship a fake KASE API and does not claim that
KASE is connected. KASE distributes market data under contract; the endpoint
paths, authentication scheme and payload shape come from the contract you sign
with the exchange.

This class therefore implements the *client*: authentication, retries,
provenance, raw-payload capture and a real health probe. The endpoint map and
the field mapping are configuration (``ENDPOINTS`` and ``FIELD_MAP`` below,
overridable from ``docs/kase-integration.md``). Until they point at a real
contracted API, ``health()`` reports the API as unreachable - which is the
truth - and every getter raises ``UpstreamError`` rather than inventing data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.enums import DataMode
from app.core.errors import ConfigurationError, UpstreamError
from app.core.logging import get_logger
from app.providers.base import (
    BondDataProvider,
    ProviderBond,
    ProviderDocument,
    ProviderFinancials,
    ProviderIssuer,
    ProviderQuote,
    ProviderRating,
    ProviderStatus,
    ProviderTrade,
    Provenance,
)
from app.providers.http import HttpFetcher

logger = get_logger(__name__)

ENDPOINTS = {
    "health": "/v1/status",
    "bonds": "/v1/securities/bonds",
    "bond": "/v1/securities/bonds/{identifier}",
    "quotes": "/v1/market/quotes",
    "trades": "/v1/market/trades",
    "issuer": "/v1/issuers/{identifier}",
    "financials": "/v1/issuers/{identifier}/financials",
    "documents": "/v1/issuers/{identifier}/documents",
    "ratings": "/v1/issuers/{identifier}/ratings",
}

#: Upstream field name -> our field name. Adjust to the contracted schema.
FIELD_MAP = {
    "bond": {
        "ticker": "code",
        "isin": "isin",
        "name": "title",
        "issuer_code": "issuer_code",
        "currency": "currency",
        "nominal": "nominal_value",
        "issue_date": "issue_date",
        "maturity_date": "maturity_date",
        "coupon_rate": "coupon_rate",
        "coupon_frequency": "coupon_periods_per_year",
        "next_coupon_date": "next_coupon_date",
        "issue_size": "issue_volume",
        "outstanding_amount": "outstanding_volume",
        "market_segment": "segment",
        "bond_type": "type",
    },
    "quote": {
        "ticker": "code",
        "bid": "bid",
        "ask": "ask",
        "last": "last",
        "clean_price": "clean_price",
        "dirty_price": "dirty_price",
        "accrued_interest": "accrued_interest",
        "ytm": "yield",
        "volume": "volume",
        "turnover": "turnover",
        "number_of_trades": "deals",
        "timestamp": "timestamp",
    },
}


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    """Parse a number, returning None for anything unparseable. Never 0."""
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


class KaseApiProvider(BondDataProvider):
    name = "kase_api"
    data_mode = DataMode.LIVE.value
    is_mock = False

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ):
        self.base_url = (base_url or settings.KASE_API_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.KASE_API_KEY
        if not self.api_key:
            raise ConfigurationError(
                "KaseApiProvider requires KASE_API_KEY. Obtain credentials from "
                "KASE; this project does not ship a substitute."
            )
        self._http = HttpFetcher(
            self.base_url,
            timeout=timeout or settings.KASE_HTTP_TIMEOUT,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
        )
        self.last_raw: list[dict] = []

    def _prov(self, identifier: str | None, url: str, ts: datetime) -> Provenance:
        return Provenance(
            source=self.name,
            source_identifier=identifier,
            source_url=url,
            source_timestamp=ts,
            fetched_at=ts,
            data_mode=self.data_mode,
        )

    def _record(self, result, kind: str, key: str | None) -> None:
        self.last_raw.append(
            {
                "source": self.name,
                "kind": kind,
                "key": key,
                "url": result.url,
                "http_status": result.status,
                "content_type": result.content_type,
                "payload_json": result.json,
                "payload_hash": result.payload_hash,
                "fetched_at": result.fetched_at,
                "duration_ms": result.duration_ms,
                "data_mode": self.data_mode,
            }
        )

    async def _get(self, key: str, *, path_args: dict | None = None, params: dict | None = None):
        path = ENDPOINTS[key].format(**(path_args or {}))
        result = await self._http.fetch(path, params=params)
        self._record(result, key, (path_args or {}).get("identifier"))
        if not result.ok:
            raise UpstreamError(
                f"KASE API request failed: {key}",
                details={"url": result.url, "status": result.status, "error": result.error},
            )
        if result.json is None:
            raise UpstreamError(
                f"KASE API returned a non-JSON payload for {key}.",
                details={"url": result.url, "content_type": result.content_type},
            )
        return result

    @staticmethod
    def _rows(payload: Any) -> list[dict]:
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
        if isinstance(payload, dict):
            for key in ("data", "items", "results", "rows"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [r for r in value if isinstance(r, dict)]
        return []

    def _to_bond(self, row: dict, url: str, ts: datetime) -> ProviderBond | None:
        m = FIELD_MAP["bond"]
        ticker = row.get(m["ticker"])
        if not ticker:
            return None
        return ProviderBond(
            ticker=str(ticker),
            name=str(row.get(m["name"]) or ticker),
            issuer_code=str(row.get(m["issuer_code"]) or ""),
            isin=row.get(m["isin"]),
            currency=str(row.get(m["currency"]) or "KZT"),
            nominal=_num(row.get(m["nominal"])),
            issue_date=_parse_date(row.get(m["issue_date"])),
            maturity_date=_parse_date(row.get(m["maturity_date"])),
            coupon_rate=_num(row.get(m["coupon_rate"])),
            coupon_frequency=int(_num(row.get(m["coupon_frequency"])) or 0) or None,
            next_coupon_date=_parse_date(row.get(m["next_coupon_date"])),
            issue_size=_num(row.get(m["issue_size"])),
            outstanding_amount=_num(row.get(m["outstanding_amount"])),
            market_segment=row.get(m["market_segment"]),
            bond_type=row.get(m["bond_type"]),
            provenance=self._prov(str(ticker), url, ts),
        )

    # -- bonds -----------------------------------------------------------

    async def get_bonds(self) -> list[ProviderBond]:
        result = await self._get("bonds")
        bonds = [
            b
            for b in (
                self._to_bond(row, result.url, result.fetched_at)
                for row in self._rows(result.json)
            )
            if b is not None
        ]
        return bonds

    async def get_bond(self, identifier: str) -> ProviderBond | None:
        result = await self._get("bond", path_args={"identifier": identifier})
        payload = result.json
        row = payload if isinstance(payload, dict) and "code" in payload else None
        if row is None:
            rows = self._rows(payload)
            row = rows[0] if rows else None
        if row is None:
            return None
        return self._to_bond(row, result.url, result.fetched_at)

    async def search_bonds(self, query: str) -> list[ProviderBond]:
        result = await self._get("bonds", params={"q": query})
        return [
            b
            for b in (
                self._to_bond(row, result.url, result.fetched_at)
                for row in self._rows(result.json)
            )
            if b is not None
        ]

    # -- market ----------------------------------------------------------

    async def get_quotes(self, tickers: list[str] | None = None) -> list[ProviderQuote]:
        params = {"codes": ",".join(tickers)} if tickers else None
        result = await self._get("quotes", params=params)
        m = FIELD_MAP["quote"]
        quotes: list[ProviderQuote] = []
        for row in self._rows(result.json):
            ticker = row.get(m["ticker"])
            if not ticker:
                continue
            quotes.append(
                ProviderQuote(
                    ticker=str(ticker),
                    timestamp=_parse_dt(row.get(m["timestamp"])) or result.fetched_at,
                    bid=_num(row.get(m["bid"])),
                    ask=_num(row.get(m["ask"])),
                    last=_num(row.get(m["last"])),
                    clean_price=_num(row.get(m["clean_price"])),
                    dirty_price=_num(row.get(m["dirty_price"])),
                    accrued_interest=_num(row.get(m["accrued_interest"])),
                    ytm=_num(row.get(m["ytm"])),
                    volume=_num(row.get(m["volume"])),
                    turnover=_num(row.get(m["turnover"])),
                    number_of_trades=int(_num(row.get(m["number_of_trades"])) or 0) or None,
                    provenance=self._prov(str(ticker), result.url, result.fetched_at),
                )
            )
        return quotes

    async def get_trades(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[ProviderTrade]:
        params: dict[str, Any] = {"code": ticker}
        if since:
            params["from"] = since.isoformat()
        result = await self._get("trades", params=params)
        trades: list[ProviderTrade] = []
        for row in self._rows(result.json):
            trades.append(
                ProviderTrade(
                    ticker=ticker,
                    timestamp=_parse_dt(row.get("timestamp")) or result.fetched_at,
                    trade_id=row.get("id"),
                    price=_num(row.get("price")),
                    clean_price=_num(row.get("clean_price")),
                    ytm=_num(row.get("yield")),
                    quantity=_num(row.get("quantity")),
                    amount=_num(row.get("amount")),
                    currency=row.get("currency"),
                    provenance=self._prov(ticker, result.url, result.fetched_at),
                )
            )
        return trades

    # -- issuer ----------------------------------------------------------

    async def get_issuer(self, identifier: str) -> ProviderIssuer | None:
        result = await self._get("issuer", path_args={"identifier": identifier})
        row = result.json if isinstance(result.json, dict) else None
        if not row:
            return None
        sector = row.get("sector")
        return ProviderIssuer(
            code=str(row.get("code") or identifier).upper(),
            name=str(row.get("name") or identifier),
            short_name=row.get("short_name"),
            bin=row.get("bin"),
            sector=sector,
            industry=row.get("industry"),
            is_financial_institution=sector in ("bank", "financial"),
            is_state_owned=bool(row.get("state_owned")),
            website=row.get("website"),
            kase_url=row.get("url"),
            provenance=self._prov(identifier, result.url, result.fetched_at),
        )

    async def get_financials(self, issuer_code: str) -> list[ProviderFinancials]:
        result = await self._get("financials", path_args={"identifier": issuer_code})
        statements: list[ProviderFinancials] = []
        for row in self._rows(result.json):
            period_end = _parse_date(row.get("period_end"))
            if period_end is None:
                continue
            values = {
                key: _num(value)
                for key, value in row.items()
                if key not in ("period_end", "period_type", "currency")
            }
            statements.append(
                ProviderFinancials(
                    issuer_code=issuer_code.upper(),
                    period_end=period_end,
                    period_type=str(row.get("period_type") or "FY"),
                    currency=str(row.get("currency") or "KZT"),
                    values=values,
                    provenance=self._prov(issuer_code, result.url, result.fetched_at),
                )
            )
        return statements

    async def get_documents(self, issuer_code: str) -> list[ProviderDocument]:
        result = await self._get("documents", path_args={"identifier": issuer_code})
        return [
            ProviderDocument(
                issuer_code=issuer_code.upper(),
                title=str(row.get("title") or "Документ"),
                url=str(row.get("url")),
                kind=row.get("kind"),
                published_at=_parse_date(row.get("published_at")),
                provenance=self._prov(issuer_code, result.url, result.fetched_at),
            )
            for row in self._rows(result.json)
            if row.get("url")
        ]

    async def get_ratings(self, issuer_code: str) -> list[ProviderRating]:
        result = await self._get("ratings", path_args={"identifier": issuer_code})
        return [
            ProviderRating(
                issuer_code=issuer_code.upper(),
                agency=str(row.get("agency")),
                rating=str(row.get("rating")),
                scale=row.get("scale"),
                outlook=row.get("outlook"),
                rating_date=_parse_date(row.get("date")),
                provenance=self._prov(issuer_code, result.url, result.fetched_at),
            )
            for row in self._rows(result.json)
            if row.get("agency") and row.get("rating")
        ]

    async def health(self) -> ProviderStatus:
        result = await self._http.fetch(ENDPOINTS["health"])
        self._record(result, "health", None)
        return ProviderStatus(
            name=self.name,
            reachable=result.ok,
            data_mode=self.data_mode,
            checked_at=datetime.now(timezone.utc),
            latency_ms=result.duration_ms,
            detail=(
                f"HTTP {result.status} от {self.base_url}"
                if result.ok
                else f"Недоступно: {result.error or result.status}"
            ),
            is_mock=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()
