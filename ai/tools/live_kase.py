"""Read-only live KASE data overlay for the inference tools.

The language model never receives a general HTTP client.  This store can only
talk to ``https://kase.kz`` through the verified public JSON provider used by
the backend.  Values fetched successfully replace the bundled snapshot in an
in-process TTL cache; on an outage the snapshot remains available with its
original provenance and date.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import fields
from datetime import date, datetime, timezone
from typing import Any, Coroutine, TypeVar
from urllib.parse import urlparse

from app.providers.kase_public_api import KasePublicApiProvider

from ai.tools.store import SnapshotStore

T = TypeVar("T")


def _run(coro: Coroutine[Any, Any, T]) -> T:
    """Run provider coroutines from sync inference code, even under an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[T] = []
    error: list[BaseException] = []

    def worker() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # re-raised in the caller's thread
            error.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result[0]


def _row(dto: Any) -> dict[str, Any]:
    """Flatten a provider DTO into the shape consumed by ``ToolExecutor``."""
    payload = {
        item.name: getattr(dto, item.name)
        for item in fields(dto)
        if item.name not in {"provenance", "values"}
    }
    values = getattr(dto, "values", None)
    if isinstance(values, dict):
        payload.update(values)
    provenance = getattr(dto, "provenance", None)
    if provenance is not None:
        payload.update(
            source=provenance.source,
            source_url=provenance.source_url,
            source_identifier=provenance.source_identifier,
            source_timestamp=provenance.source_timestamp,
            fetched_at=provenance.fetched_at,
            data_mode=provenance.data_mode,
        )
    return payload


class LiveKaseStore:
    """A ``DataStore`` backed by live public KASE data with snapshot fallback."""

    def __init__(
        self,
        fallback: SnapshotStore | None = None,
        *,
        base_url: str = "https://kase.kz",
        language: str = "ru",
        timeout: float = 20.0,
        ttl_seconds: float = 300.0,
    ):
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
            "kase.kz",
            "www.kase.kz",
        }:
            raise ValueError("live AI data source must be the official https://kase.kz host")
        self.fallback = fallback or SnapshotStore()
        self.base_url = base_url.rstrip("/")
        self.language = language
        self.timeout = timeout
        self.ttl_seconds = max(30.0, ttl_seconds)
        self._lock = threading.RLock()
        self._expires: dict[str, float] = {}
        self._bonds: dict[str, dict] = {}
        self._quotes: dict[str, dict] = {}
        self._issuers: dict[str, dict] = {}
        self._statements: dict[str, list[dict]] = {}
        self._cashflows: dict[str, list[dict]] = {}
        self._detailed_issuer_codes: set[str] = set()
        self._catalog_loaded = False
        self.last_error: str | None = None
        self.last_success_at: datetime | None = None

    def _fresh(self, key: str) -> bool:
        return self._expires.get(key, 0.0) > time.monotonic()

    def _mark(self, key: str, *, success: bool) -> None:
        # A failed source is retried soon, but not once per field in one answer.
        self._expires[key] = time.monotonic() + (self.ttl_seconds if success else 30.0)
        if success:
            self.last_error = None
            self.last_success_at = datetime.now(timezone.utc)

    def _provider(self) -> KasePublicApiProvider:
        return KasePublicApiProvider(
            self.base_url,
            timeout=self.timeout,
            language=self.language,
        )

    def _fetch(self, key: str, operation) -> Any:
        if self._fresh(key):
            return None

        async def request():
            provider = self._provider()
            try:
                return await operation(provider)
            finally:
                await provider.aclose()

        try:
            value = _run(request())
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._mark(key, success=False)
            return None
        self._mark(key, success=True)
        return value

    def bonds(self) -> list[dict]:
        with self._lock:
            rows = self._fetch("catalog", lambda provider: provider.get_bonds())
            if rows is not None:
                self._catalog_loaded = True
                for row in rows:
                    # Never replace a richer per-instrument response with the
                    # deliberately light catalog record.
                    self._bonds.setdefault(row.ticker.upper(), _row(row))
            if self._catalog_loaded:
                # Detailed records already fetched for a ticker win over catalog stubs.
                return list(self._bonds.values())
            merged = {
                str(row.get("ticker", "")).upper(): row for row in self.fallback.bonds()
            }
            merged.update(self._bonds)
            return list(merged.values())

    def bond(self, ticker: str) -> dict | None:
        key = (ticker or "").strip().upper()
        if not key:
            return None
        with self._lock:
            cached = self._bonds.get(key)
            if cached is None or not self._fresh(f"bond:{key}"):
                async def load(provider):
                    if key.startswith("KZ") and len(key) == 12:
                        matches = await provider.search_bonds(key)
                        return matches[0] if matches else None
                    return await provider.get_bond(key)

                dto = self._fetch(f"bond:{key}", load)
                if dto is not None:
                    cached = _row(dto)
                    self._bonds[dto.ticker.upper()] = cached
                    if dto.issuer_code:
                        self._detailed_issuer_codes.add(dto.issuer_code.upper())
            if cached is not None:
                return cached
            return self.fallback.bond(key)

    def quote(self, ticker: str) -> dict | None:
        requested = (ticker or "").strip().upper()
        bond = self._bonds.get(requested) or self.fallback.bond(requested)
        if bond is None:
            bond = self.bond(requested)
        if bond is None:
            return None
        key = str(bond["ticker"]).upper()
        with self._lock:
            if not self._fresh("quotes"):
                known_bonds = list(self._bonds.values()) or [bond]
                nominals = {
                    str(item["ticker"]).upper(): item["nominal"]
                    for item in known_bonds
                    if item.get("nominal")
                }
                rows = self._fetch(
                    "quotes",
                    lambda provider: provider.get_quotes(None, nominals=nominals),
                )
                if rows:
                    self._quotes.update({row.ticker.upper(): _row(row) for row in rows})
            return self._quotes.get(key) or self.fallback.quote(key)

    def issuer(self, code: str) -> dict | None:
        key = (code or "").strip().upper()
        if not key:
            return None
        with self._lock:
            # A catalog-wide search asks for scoring context for thousands of
            # issuer codes. Do not turn that into thousands of HTTP requests;
            # fetch issuer detail only after a specific bond was opened.
            if key not in self._detailed_issuer_codes:
                return self._issuers.get(key) or self.fallback.issuer(key)
            if key not in self._issuers or not self._fresh(f"issuer:{key}"):
                dto = self._fetch(f"issuer:{key}", lambda provider: provider.get_issuer(key))
                if dto is not None:
                    self._issuers[key] = _row(dto)
            return self._issuers.get(key) or self.fallback.issuer(key)

    def issuers(self) -> list[dict]:
        fallback = getattr(self.fallback, "issuers", lambda: [])()
        merged = {str(row.get("code", "")).upper(): row for row in fallback}
        merged.update(self._issuers)
        return list(merged.values())

    def statements(self, issuer_code: str) -> list[dict]:
        key = (issuer_code or "").strip().upper()
        with self._lock:
            if key not in self._detailed_issuer_codes:
                return list(self._statements.get(key) or self.fallback.statements(key))
            if key not in self._statements or not self._fresh(f"financials:{key}"):
                rows = self._fetch(
                    f"financials:{key}", lambda provider: provider.get_financials(key)
                )
                if rows:
                    parsed = [_row(row) for row in rows]
                    parsed.sort(key=lambda row: row.get("period_end") or date.min, reverse=True)
                    self._statements[key] = parsed
            return list(self._statements.get(key) or self.fallback.statements(key))

    def cashflows(self, ticker: str) -> list[dict]:
        bond = self.bond(ticker)
        if bond is None:
            return []
        key = str(bond["ticker"]).upper()
        with self._lock:
            if key not in self._cashflows or not self._fresh(f"cashflows:{key}"):
                periods = self._fetch(
                    f"cashflows:{key}",
                    lambda provider: provider.get_coupon_schedule(key),
                )
                if periods:
                    frequency = bond.get("coupon_frequency") or 1
                    nominal = bond.get("nominal") or 100.0
                    maturity = bond.get("maturity_date")
                    rows = []
                    for period in periods:
                        rate = period.rate if period.rate is not None else bond.get("coupon_rate")
                        coupon = None if rate is None else nominal * rate / frequency
                        principal = nominal if maturity and period.payment_date >= maturity else 0.0
                        row = _row(period)
                        row.update(
                            coupon_amount=coupon,
                            principal_amount=principal,
                            total_amount=None if coupon is None else coupon + principal,
                            is_estimated=period.rate is None,
                            is_final=bool(principal),
                        )
                        rows.append(row)
                    self._cashflows[key] = rows
            return list(self._cashflows.get(key) or self.fallback.cashflows(key))

    def inflation(self) -> dict | None:
        # Inflation is not a KASE dataset; retain the separately sourced snapshot.
        return self.fallback.inflation()

    def curve(self) -> list[dict]:
        return self.fallback.curve()

    def bonds_with_quotes(self):
        for bond in self.bonds():
            quote = self.quote(bond["ticker"])
            if quote is not None:
                yield bond, quote

    @staticmethod
    def provenance(row: dict | None):
        return SnapshotStore.provenance(row)

    def status(self) -> dict[str, Any]:
        return {
            "mode": "live",
            "source": self.base_url,
            "host_restricted": True,
            "last_success_at": self.last_success_at.isoformat()
            if self.last_success_at
            else None,
            "last_error": self.last_error,
            "ttl_seconds": self.ttl_seconds,
        }


__all__ = ["LiveKaseStore"]
