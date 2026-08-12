"""Composite provider: try authoritative sources first, in order.

Fallback rules that are enforced here:

* A mock provider may only be used as a fallback outside production.
* Falling back is always visible: ``last_source`` and the DTO provenance record
  which provider actually answered, and mock answers keep ``data_mode="mock"``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import settings
from app.core.errors import MockDataForbiddenError, UpstreamError
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
)

logger = get_logger(__name__)


class CompositeKaseProvider(BondDataProvider):
    name = "composite_kase"

    def __init__(self, providers: list[BondDataProvider]):
        if not providers:
            raise ValueError("CompositeKaseProvider needs at least one provider")
        if settings.is_production and any(p.is_mock for p in providers):
            raise MockDataForbiddenError(
                "A mock provider was placed in the production provider chain."
            )
        self.providers = providers
        self.last_source: str | None = None
        self.errors: list[dict] = []

    @property
    def data_mode(self) -> str:  # type: ignore[override]
        for provider in self.providers:
            if provider.name == self.last_source:
                return provider.data_mode
        return self.providers[0].data_mode

    @property
    def is_mock(self) -> bool:  # type: ignore[override]
        for provider in self.providers:
            if provider.name == self.last_source:
                return provider.is_mock
        return False

    async def _first(self, method: str, *args, **kwargs):
        self.errors = []
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                value = await getattr(provider, method)(*args, **kwargs)
            except Exception as exc:
                logger.warning("provider %s failed on %s: %s", provider.name, method, exc)
                self.errors.append({"provider": provider.name, "error": str(exc)})
                last_error = exc
                continue
            if value in (None, [], {}):
                self.errors.append({"provider": provider.name, "error": "empty result"})
                continue
            self.last_source = provider.name
            if provider.is_mock:
                logger.warning(
                    "serving MOCK data for %s from %s - development only",
                    method,
                    provider.name,
                )
            return value
        if last_error is not None:
            raise UpstreamError(
                f"All providers failed for {method}.", details={"attempts": self.errors}
            )
        return [] if method.startswith("get_") and method != "get_bond" else None

    async def get_bonds(self) -> list[ProviderBond]:
        return await self._first("get_bonds")

    async def get_bond(self, identifier: str) -> ProviderBond | None:
        return await self._first("get_bond", identifier)

    async def search_bonds(self, query: str) -> list[ProviderBond]:
        return await self._first("search_bonds", query)

    async def get_quotes(self, tickers: list[str] | None = None) -> list[ProviderQuote]:
        return await self._first("get_quotes", tickers)

    async def get_trades(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[ProviderTrade]:
        return await self._first("get_trades", ticker, since=since)

    async def get_issuer(self, identifier: str) -> ProviderIssuer | None:
        return await self._first("get_issuer", identifier)

    async def get_financials(self, issuer_code: str) -> list[ProviderFinancials]:
        return await self._first("get_financials", issuer_code)

    async def get_documents(self, issuer_code: str) -> list[ProviderDocument]:
        return await self._first("get_documents", issuer_code)

    async def get_ratings(self, issuer_code: str) -> list[ProviderRating]:
        return await self._first("get_ratings", issuer_code)

    async def health(self) -> ProviderStatus:
        statuses = []
        for provider in self.providers:
            try:
                statuses.append(await provider.health())
            except Exception as exc:
                statuses.append(
                    ProviderStatus(
                        name=provider.name,
                        reachable=False,
                        data_mode=provider.data_mode,
                        checked_at=datetime.now(timezone.utc),
                        detail=str(exc),
                        is_mock=provider.is_mock,
                    )
                )
        self.sub_statuses = statuses
        live = next((s for s in statuses if s.reachable and not s.is_mock), None)
        chosen = live or next((s for s in statuses if s.reachable), None)
        return ProviderStatus(
            name=self.name,
            reachable=chosen is not None,
            data_mode=chosen.data_mode if chosen else "unavailable",
            checked_at=datetime.now(timezone.utc),
            latency_ms=chosen.latency_ms if chosen else None,
            detail="; ".join(f"{s.name}: {s.detail}" for s in statuses),
            is_mock=bool(chosen and chosen.is_mock),
        )

    async def aclose(self) -> None:
        for provider in self.providers:
            await provider.aclose()
