"""Idempotent document versioning and news ingestion."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.incremental import DocumentVersion, KaseDocument, KaseNewsItem
from app.services.incremental import IncrementalStateService, content_hash, normalize_url

FetchDocument = Callable[[str], Awaitable[tuple[bytes, dict[str, str]]]]


async def default_fetch_document(url: str) -> tuple[bytes, dict[str, str]]:
    async with httpx.AsyncClient(
        timeout=settings.KASE_HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": settings.BROWSER_USER_AGENT},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        if not str(response.url).startswith(("https://kase.kz/", "https://www.kase.kz/")):
            raise ValueError("document redirect left the official KASE host")
        return response.content, dict(response.headers)


class DocumentIngestionService:
    def __init__(self, session: Session, storage_dir: str | Path = "var/documents"):
        self.session = session
        self.storage_dir = Path(storage_dir)
        self.states = IncrementalStateService(session)

    async def ingest(
        self,
        *,
        entity_id: str,
        documents: list[dict[str, Any]],
        ticker: str | None = None,
        issuer_code: str | None = None,
        fetch: FetchDocument = default_fetch_document,
    ) -> dict[str, int]:
        metrics = {"new_documents": 0, "new_versions": 0, "documents_skipped": 0, "ai_tasks_created": 0}
        now = datetime.now(timezone.utc)
        for item in documents:
            url = item.get("document_url") or item.get("url")
            if not url:
                continue
            url = normalize_url(url)
            doc = self.session.execute(select(KaseDocument).where(KaseDocument.document_url == url)).scalar_one_or_none()
            if doc is None:
                doc = KaseDocument(
                    entity_id=entity_id, ticker=ticker, issuer_code=issuer_code,
                    document_url=url,
                    document_name=item.get("document_name") or item.get("name") or url.rsplit("/", 1)[-1],
                    document_type=item.get("document_type") or item.get("kind"),
                    publication_date=_datetime(item.get("publication_date") or item.get("published_at")),
                    last_checked_at=now, last_changed_at=now,
                )
                self.session.add(doc)
                self.session.flush()
                metrics["new_documents"] += 1
            else:
                doc.last_checked_at = now

            latest = self.session.execute(select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id
            ).order_by(desc(DocumentVersion.version_number)).limit(1)).scalar_one_or_none()
            hinted_etag = item.get("etag")
            hinted_modified = item.get("last_modified")
            hinted_size = item.get("file_size")
            if latest and (
                (hinted_etag and hinted_etag == latest.etag)
                or (hinted_modified and hinted_modified == latest.last_modified and hinted_size == latest.file_size)
            ):
                metrics["documents_skipped"] += 1
                continue

            body, headers = await fetch(url)
            digest = hashlib.sha256(body).hexdigest()
            if latest and latest.content_hash == digest:
                latest.etag = headers.get("etag") or hinted_etag or latest.etag
                latest.last_modified = headers.get("last-modified") or hinted_modified or latest.last_modified
                metrics["documents_skipped"] += 1
                continue

            self.storage_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(url.split("?", 1)[0]).suffix[:10] or ".bin"
            path = self.storage_dir / f"{doc.id}-{(latest.version_number + 1) if latest else 1}-{digest[:12]}{suffix}"
            path.write_bytes(body)
            version = DocumentVersion(
                document_id=doc.id, version_number=(latest.version_number + 1) if latest else 1,
                file_size=len(body), etag=headers.get("etag") or hinted_etag,
                last_modified=headers.get("last-modified") or hinted_modified,
                content_hash=digest, downloaded_at=now, storage_path=str(path),
                analysis_status="pending",
            )
            self.session.add(version)
            self.session.flush()
            doc.current_version_id = version.id
            doc.last_changed_at = now
            outcome = self.states.process(
                entity_type="document", entity_id=str(doc.id), ticker=ticker,
                section="documents", payload={
                    "document_id": doc.id, "url": url, "name": doc.document_name,
                    "version": version.version_number, "content_hash": digest,
                    "file_size": len(body),
                }, source_url=url, etag=version.etag, last_modified=version.last_modified,
            )
            metrics["new_versions"] += 1
            metrics["ai_tasks_created"] += len(outcome.plan.ai_tasks)
        self.session.flush()
        return metrics


class NewsIngestionService:
    def __init__(self, session: Session):
        self.session = session
        self.states = IncrementalStateService(session)

    def ingest(self, *, entity_id: str, items: list[dict[str, Any]], ticker: str | None = None, issuer_code: str | None = None) -> dict[str, int]:
        created = analyzed = 0
        for item in items:
            stable = item.get("id") or item.get("stable_identifier")
            title = " ".join(str(item.get("title") or "").split())
            published = _datetime(item.get("publication_date") or item.get("published_at"))
            url = item.get("url") or ""
            fingerprint = content_hash({
                "stable": stable, "title": title.casefold(),
                "publication_date": published, "issuer": issuer_code, "url": url,
            })
            exists = self.session.execute(select(KaseNewsItem).where(KaseNewsItem.fingerprint == fingerprint)).scalar_one_or_none()
            if exists:
                continue
            row = KaseNewsItem(
                entity_id=entity_id, ticker=ticker, issuer_code=issuer_code,
                stable_identifier=str(stable) if stable is not None else None,
                fingerprint=fingerprint, title=title, publication_date=published,
                url=url, content_hash=content_hash(item),
            )
            self.session.add(row)
            self.session.flush()
            outcome = self.states.process(
                entity_type="news", entity_id=str(row.id), ticker=ticker, section="news",
                payload={"fingerprint": fingerprint, "title": title, "publication_date": published, "url": url},
                source_url=url or settings.KASE_WEBSITE_URL, source_timestamp=published,
            )
            created += 1
            analyzed += len(outcome.plan.ai_tasks)
        self.session.flush()
        from app.services.stock_actions import StockActionIngestionService
        actions = StockActionIngestionService(self.session).ingest(ticker=ticker, items=items)
        return {"new_news": created, "ai_tasks_created": analyzed, **actions}


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


__all__ = ["DocumentIngestionService", "NewsIngestionService", "default_fetch_document"]
