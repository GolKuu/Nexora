"""Offline mode: serve the last verified data, contact nothing.

This provider exists for the case where KASE is unreachable - blocked,
rate-limiting, down, or simply not available from where the service runs. It
makes **no network calls at all** and fabricates nothing. Every ingestion
method returns empty, because there is genuinely nothing new to ingest; the
API keeps answering from the database, which is where it already reads from.

What this is not:

* It is not a mock. ``MockKaseProvider`` invents numbers and is refused in
  production; this one invents nothing and is safe in production.
* It is not a claim that KASE is connected. :meth:`health` reports
  ``reachable=False`` with ``mode="cache"``, and the served data carries
  ``data_mode="cached"`` plus its real age.

The data it serves is real KASE data that was fetched earlier. The honest
statement is "this is from <timestamp>", and that is what the API says.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums import DataMode
from app.core.logging import get_logger
from app.providers.base import (
    BondDataProvider,
    ProviderBond,
    ProviderCouponPeriod,
    ProviderDocument,
    ProviderFinancials,
    ProviderIssuer,
    ProviderQuote,
    ProviderRating,
    ProviderStatus,
    ProviderTrade,
)

logger = get_logger(__name__)


class OfflineCacheProvider(BondDataProvider):
    """A provider that never leaves the machine."""

    name = "offline_cache"
    data_mode = DataMode.CACHED.value
    #: Not mock: nothing here is invented, so production may use it.
    is_mock = False

    def __init__(self, reason: str | None = None):
        self.reason = reason or (
            "Работа в офлайн-режиме: обращения к KASE отключены, "
            "используются последние проверенные данные из базы."
        )

    async def get_bonds(self) -> list[ProviderBond]:
        return []

    async def get_bond(self, identifier: str) -> ProviderBond | None:
        return None

    async def search_bonds(self, query: str) -> list[ProviderBond]:
        return []

    async def get_quotes(self, tickers=None, **kwargs) -> list[ProviderQuote]:
        return []

    async def get_trades(self, ticker: str, *, since=None) -> list[ProviderTrade]:
        return []

    async def get_coupon_schedule(self, identifier: str) -> list[ProviderCouponPeriod]:
        return []

    async def get_issuer(self, identifier: str) -> ProviderIssuer | None:
        return None

    async def get_financials(self, issuer_code: str) -> list[ProviderFinancials]:
        return []

    async def get_documents(self, issuer_code: str) -> list[ProviderDocument]:
        return []

    async def get_ratings(self, issuer_code: str) -> list[ProviderRating]:
        return []

    async def health(self) -> ProviderStatus:
        """Honest by construction: nothing was contacted, so nothing is up."""
        return ProviderStatus(
            name=self.name,
            reachable=False,
            data_mode=self.data_mode,
            checked_at=datetime.now(timezone.utc),
            latency_ms=0.0,
            detail=self.reason,
            is_mock=False,
        )
