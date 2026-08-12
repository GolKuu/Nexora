"""Shared HTTP plumbing for the live providers."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

USER_AGENT = "KASE-Bond-AI/0.1 (+https://github.com/; contact: set in .env)"


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int | None
    text: str | None
    json: Any | None
    content_type: str | None
    fetched_at: datetime
    duration_ms: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def payload_hash(self) -> str | None:
        if self.text is None:
            return None
        return hashlib.sha256(self.text.encode("utf-8", "ignore")).hexdigest()


class HttpFetcher:
    def __init__(self, base_url: str, *, timeout: float = 15.0, headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
        )

    async def fetch(self, path: str, *, params: dict | None = None) -> FetchResult:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        started = time.perf_counter()
        try:
            response = await self._client.get(url, params=params)
        except Exception as exc:  # network failure is data, not a crash
            duration = (time.perf_counter() - started) * 1000
            logger.warning("fetch failed url=%s error=%s", url, exc)
            return FetchResult(
                url=url, status=None, text=None, json=None, content_type=None,
                fetched_at=datetime.now(timezone.utc), duration_ms=duration, error=str(exc),
            )
        duration = (time.perf_counter() - started) * 1000
        content_type = response.headers.get("content-type", "")
        payload = None
        if "json" in content_type:
            try:
                payload = response.json()
            except ValueError:
                payload = None
        return FetchResult(
            url=url,
            status=response.status_code,
            text=response.text,
            json=payload,
            content_type=content_type,
            fetched_at=datetime.now(timezone.utc),
            duration_ms=duration,
            error=None if response.status_code < 400 else f"HTTP {response.status_code}",
        )

    async def aclose(self) -> None:
        await self._client.aclose()
