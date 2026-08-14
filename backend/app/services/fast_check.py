"""Cheap HTTP/metadata gate before a Chromium deep extraction."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.incremental import SourceCheckLog
from app.services.incremental import IncrementalStateService, PARSER_VERSION

SCRIPT_RE = re.compile(r"<(?:script|style)\b[^>]*>.*?</(?:script|style)>", re.I | re.S)
NOISE_RE = re.compile(
    r"(?:csrf(?:token)?|session(?:id)?|nonce|request-id|data-reactid)\s*[=:]\s*[\"']?[^\s\"'<>]+",
    re.I,
)
TIME_RE = re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\.\d+)?\b")


def normalize_html_for_check(html: str) -> str:
    text = SCRIPT_RE.sub("", html)
    text = NOISE_RE.sub("", text)
    text = TIME_RE.sub("<time>", text)
    return " ".join(text.split())


@dataclass(slots=True)
class FastCheckResult:
    status: str
    changed: bool
    source_url: str
    http_status: int | None = None
    latency_ms: float | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    reason: str | None = None


class FastCheckService:
    def __init__(self, session: Session):
        self.session = session
        self.states = IncrementalStateService(session)

    async def check(
        self,
        *,
        entity_type: str,
        entity_id: str,
        url: str,
        ticker: str | None = None,
        isin: str | None = None,
        force: bool = False,
        job_id: int | None = None,
    ) -> FastCheckResult:
        previous = self.states.latest(entity_type, entity_id, "page_metadata")
        if force:
            return FastCheckResult("forced", True, url, reason="force=true")
        if previous and previous.parser_version != PARSER_VERSION:
            return FastCheckResult("parser_changed", True, url, reason="parser_version changed")
        if previous:
            checked = previous.last_checked_at
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - checked >= timedelta(
                hours=settings.INCREMENTAL_FORCE_FULL_AFTER_HOURS
            ):
                return FastCheckResult(
                    "periodic_validation", True, url,
                    reason="forced full validation interval elapsed",
                )

        started = time.perf_counter()
        headers = {
            "User-Agent": settings.BROWSER_USER_AGENT,
            "Accept-Language": f"{settings.KASE_LANGUAGE},ru;q=0.9,en;q=0.7",
        }
        try:
            async with httpx.AsyncClient(
                timeout=settings.INCREMENTAL_FAST_CHECK_TIMEOUT,
                follow_redirects=True,
                headers=headers,
            ) as client:
                head = await client.head(url)
                etag = head.headers.get("etag")
                modified = head.headers.get("last-modified")
                latency = (time.perf_counter() - started) * 1000
                metadata_same = False
                if previous:
                    if etag and previous.etag:
                        metadata_same = etag == previous.etag
                    elif modified and previous.last_modified:
                        metadata_same = modified == previous.last_modified
                if previous and metadata_same:
                    result = self.states.process(
                        entity_type=entity_type, entity_id=entity_id, section="page_metadata",
                        payload=previous.normalized_json, source_url=url, ticker=ticker, isin=isin,
                        etag=etag, last_modified=modified, checked_at=datetime.now(timezone.utc),
                        job_id=job_id,
                    )
                    self._enrich_log(http_status=head.status_code, latency_ms=latency)
                    return FastCheckResult("unchanged", False, url, head.status_code, latency, etag, modified, previous.content_hash, "HTTP metadata matched")

                response = await client.get(url)
                response.raise_for_status()
                etag = response.headers.get("etag") or etag
                modified = response.headers.get("last-modified") or modified
                normalized = normalize_html_for_check(response.text)
                result = self.states.process(
                    entity_type=entity_type, entity_id=entity_id, section="page_metadata",
                    payload={"html": normalized}, source_url=str(response.url), ticker=ticker,
                    isin=isin, etag=etag, last_modified=modified,
                    checked_at=datetime.now(timezone.utc), job_id=job_id,
                )
                self.session.flush()
                elapsed = (time.perf_counter() - started) * 1000
                self._enrich_log(http_status=response.status_code, latency_ms=elapsed)
                current = self.states.latest(entity_type, entity_id, "page_metadata")
                return FastCheckResult(
                    result.status, result.status != "unchanged", str(response.url),
                    response.status_code, elapsed,
                    etag, modified,
                    current.content_hash if current else None,
                    "normalized content hash",
                )
        except Exception as exc:
            # Trust rule: an unreliable cheap check must lead to a deep check.
            return FastCheckResult(
                "uncertain", True, url, latency_ms=(time.perf_counter() - started) * 1000,
                reason=f"{type(exc).__name__}: {exc}",
            )

    def _enrich_log(self, *, http_status: int, latency_ms: float) -> None:
        row = self.session.execute(
            select(SourceCheckLog).order_by(desc(SourceCheckLog.id)).limit(1)
        ).scalar_one_or_none()
        if row is not None:
            row.http_status = http_status
            row.latency_ms = latency_ms
            self.session.flush()


__all__ = ["FastCheckResult", "FastCheckService", "normalize_html_for_check"]
