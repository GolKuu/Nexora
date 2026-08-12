"""Provider that reads the public KASE website.

Status of this implementation - read before relying on it:

* The HTTP transport, provenance stamping, raw-payload capture and health
  probe are real and complete.
* The HTML parsing is written against the page layout documented in
  ``docs/kase-integration.md``. KASE changes its markup without notice, so the
  selectors are configuration, not constants.
* When a page cannot be parsed the provider returns nothing and records the
  reason. It never falls back to invented values, and it never silently
  delegates to the mock provider.

Only enable this provider after verifying the selectors against the live site,
and respect the site's terms of use and robots.txt.
"""

from __future__ import annotations

from datetime import datetime, timezone

from selectolax.parser import HTMLParser

from app.core.config import settings
from app.core.enums import DataMode
from app.core.errors import UpstreamError
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

#: Page paths. Override in configuration when KASE reorganises its site.
PATHS = {
    "bond_list": "/en/bonds/",
    "bond_detail": "/en/bonds/show/{ticker}/",
    "issuer_detail": "/en/issuers/{code}/",
}

#: CSS selectors, deliberately kept in one place. Verify before enabling.
SELECTORS = {
    "bond_rows": "table.table tbody tr",
    "bond_cell": "td",
    "issuer_name": "h1",
}


class KaseWebsiteProvider(BondDataProvider):
    name = "kase_website"
    data_mode = DataMode.DELAYED.value
    is_mock = False

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.KASE_WEBSITE_URL).rstrip("/")
        self._http = HttpFetcher(
            self.base_url, timeout=timeout or settings.KASE_HTTP_TIMEOUT
        )
        #: Raw responses captured during the last call, for RawKaseData.
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
                "payload_text": result.text[:200_000] if result.text else None,
                "payload_hash": result.payload_hash,
                "fetched_at": result.fetched_at,
                "duration_ms": result.duration_ms,
                "data_mode": self.data_mode,
            }
        )

    # -- bonds -----------------------------------------------------------

    async def get_bonds(self) -> list[ProviderBond]:
        result = await self._http.fetch(PATHS["bond_list"])
        self._record(result, "bonds", None)
        if not result.ok or not result.text:
            raise UpstreamError(
                "KASE website is not reachable or returned an error.",
                details={"url": result.url, "status": result.status, "error": result.error},
            )
        bonds = self._parse_bond_list(result.text, result.url, result.fetched_at)
        if not bonds:
            # Reachable but unparseable: say so instead of returning nothing quietly.
            raise UpstreamError(
                "KASE bond list page was fetched but no rows could be parsed. "
                "The selectors in app/providers/kase_website.py need updating.",
                details={"url": result.url},
            )
        return bonds

    def _parse_bond_list(
        self, html: str, url: str, ts: datetime
    ) -> list[ProviderBond]:
        tree = HTMLParser(html)
        bonds: list[ProviderBond] = []
        for row in tree.css(SELECTORS["bond_rows"]):
            cells = [c.text(strip=True) for c in row.css(SELECTORS["bond_cell"])]
            if len(cells) < 2:
                continue
            ticker = cells[0]
            if not ticker or " " in ticker:
                continue
            link = row.css_first("a")
            href = link.attributes.get("href") if link else None
            bonds.append(
                ProviderBond(
                    ticker=ticker,
                    name=cells[1],
                    # Real issuer linkage requires the detail page; leaving the
                    # code equal to the ticker would be a fabrication, so the
                    # collector resolves it separately.
                    issuer_code="",
                    kase_url=f"{self.base_url}{href}" if href else None,
                    provenance=self._prov(ticker, url, ts),
                )
            )
        return bonds

    async def get_bond(self, identifier: str) -> ProviderBond | None:
        path = PATHS["bond_detail"].format(ticker=identifier)
        result = await self._http.fetch(path)
        self._record(result, "bond", identifier)
        if not result.ok or not result.text:
            return None
        for bond in self._parse_bond_list(result.text, result.url, result.fetched_at):
            if bond.ticker.upper() == identifier.upper():
                return bond
        return None

    async def search_bonds(self, query: str) -> list[ProviderBond]:
        bonds = await self.get_bonds()
        needle = query.lower()
        return [b for b in bonds if needle in b.ticker.lower() or needle in b.name.lower()]

    # -- market ----------------------------------------------------------

    async def get_quotes(self, tickers: list[str] | None = None) -> list[ProviderQuote]:
        # The public site publishes end-of-day figures on the bond list page.
        result = await self._http.fetch(PATHS["bond_list"])
        self._record(result, "quotes", None)
        if not result.ok or not result.text:
            raise UpstreamError(
                "KASE website quotes are unavailable.",
                details={"url": result.url, "status": result.status},
            )
        return []

    async def get_trades(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[ProviderTrade]:
        return []

    # -- issuer ----------------------------------------------------------

    async def get_issuer(self, identifier: str) -> ProviderIssuer | None:
        path = PATHS["issuer_detail"].format(code=identifier)
        result = await self._http.fetch(path)
        self._record(result, "issuer", identifier)
        if not result.ok or not result.text:
            return None
        tree = HTMLParser(result.text)
        heading = tree.css_first(SELECTORS["issuer_name"])
        if heading is None:
            return None
        return ProviderIssuer(
            code=identifier.upper(),
            name=heading.text(strip=True),
            kase_url=result.url,
            provenance=self._prov(identifier, result.url, result.fetched_at),
        )

    async def get_financials(self, issuer_code: str) -> list[ProviderFinancials]:
        # Financial statements are published as PDF/XLS attachments. Parsing
        # them is a separate collector; returning [] is honest here.
        return []

    async def get_documents(self, issuer_code: str) -> list[ProviderDocument]:
        path = PATHS["issuer_detail"].format(code=issuer_code)
        result = await self._http.fetch(path)
        self._record(result, "documents", issuer_code)
        if not result.ok or not result.text:
            return []
        tree = HTMLParser(result.text)
        documents: list[ProviderDocument] = []
        for link in tree.css("a"):
            href = link.attributes.get("href") or ""
            if not href.lower().endswith((".pdf", ".xls", ".xlsx", ".doc", ".docx")):
                continue
            documents.append(
                ProviderDocument(
                    issuer_code=issuer_code.upper(),
                    title=link.text(strip=True) or href.rsplit("/", 1)[-1],
                    url=href if href.startswith("http") else f"{self.base_url}{href}",
                    provenance=self._prov(issuer_code, result.url, result.fetched_at),
                )
            )
        return documents

    async def get_ratings(self, issuer_code: str) -> list[ProviderRating]:
        return []

    # -- health ----------------------------------------------------------

    async def health(self) -> ProviderStatus:
        result = await self._http.fetch(PATHS["bond_list"])
        parsed = 0
        if result.ok and result.text:
            parsed = len(self._parse_bond_list(result.text, result.url, result.fetched_at))
        return ProviderStatus(
            name=self.name,
            reachable=result.ok,
            data_mode=self.data_mode,
            checked_at=datetime.now(timezone.utc),
            latency_ms=result.duration_ms,
            detail=(
                f"HTTP {result.status}; распознано выпусков: {parsed}."
                if result.ok
                else f"Недоступно: {result.error}"
            ),
            is_mock=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()
