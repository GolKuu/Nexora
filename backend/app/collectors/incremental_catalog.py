"""Incremental KASE catalog synchronization."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.collectors.kase_collector import KaseCollector, sync_issuer_details
from app.providers.base import BondDataProvider, ProviderIssuer
from app.services.incremental_documents import DocumentIngestionService
from app.services.incremental import IncrementalStateService, JobTracker


async def incremental_catalog_sync(
    session: Session,
    provider: BondDataProvider,
    *,
    force: bool = False,
    idempotency_key: str | None = None,
) -> dict:
    job = JobTracker(session, "catalog", idempotency_key or f"catalog:{uuid4().hex}")
    if job.reused and job.row.status == "completed":
        return {**(job.row.metrics_json or {}), "status": "already_completed", "job_id": job.row.id}

    collector = KaseCollector(session, provider)
    states = IncrementalStateService(session)
    catalog = await provider.get_bonds()
    collector._flush_raw()
    known = {bond.ticker.upper(): bond for bond in collector.bonds.list(active_only=False, limit=10000)}
    known_isin = {bond.isin.upper(): bond for bond in known.values() if bond.isin}
    seen: set[str] = set()
    metrics = {
        "entities_checked": len(catalog), "entities_changed": 0, "entities_unchanged": 0,
        "entities_failed": 0, "new_records": 0, "updated_records": 0,
        "inactive_records": 0, "deep_extractions": 0, "ai_tasks_created": 0,
        "financial_statements": 0, "ratings": 0, "new_documents": 0,
        "document_versions": 0, "documents_skipped": 0,
    }

    # Refuse to deactivate the world on a partial/error response.
    catalog_valid = bool(catalog) and (not known or len(catalog) >= max(1, int(len(known) * 0.5)))
    issuer_cache: dict[str, int] = {}
    for stub in catalog:
        key = stub.ticker.upper()
        seen.add(key)
        existing = known.get(key) or (known_isin.get(stub.isin.upper()) if stub.isin else None)
        if existing is not None:
            seen.add(existing.ticker.upper())
            if existing.ticker.upper() != key and stub.isin and existing.isin == stub.isin:
                existing.ticker = stub.ticker
                session.flush()
        profile = {
            "ticker": stub.ticker, "name": stub.name, "issuer_code": stub.issuer_code,
            "currency": stub.currency, "maturity_date": stub.maturity_date,
            "issue_size": stub.issue_size, "outstanding_amount": stub.outstanding_amount,
            "market_segment": stub.market_segment, "bond_type": stub.bond_type,
            "is_active": stub.is_active,
        }
        outcome = None
        if existing is not None:
            outcome = states.process(
                entity_type="bond", entity_id=str(existing.id), ticker=stub.ticker,
                isin=existing.isin, section="bond_profile", payload=profile,
                source_url=stub.kase_url or "https://kase.kz/",
                source_timestamp=stub.provenance.source_timestamp if stub.provenance else None,
                job_id=job.row.id,
            )
            if outcome.status == "unchanged" and not force:
                existing.last_checked_at = datetime.now(timezone.utc)
                metrics["entities_unchanged"] += 1
                continue
            if outcome.status == "anomaly":
                metrics["entities_failed"] += 1
                continue

        detail = await provider.get_bond(stub.ticker)
        collector._flush_raw()
        dto = detail or stub
        code = (dto.issuer_code or "").strip()
        if not code:
            metrics["entities_failed"] += 1
            continue
        new_issuer = code not in issuer_cache
        if new_issuer:
            issuer = await provider.get_issuer(code)
            collector._flush_raw()
            issuer = issuer or ProviderIssuer(code=code, name=code)
            issuer_cache[code] = collector._save_issuer(issuer)
        row_id = collector._save_bond(dto, issuer_cache[code])
        row = collector.bonds.get(row_id)
        now = datetime.now(timezone.utc)
        row.last_checked_at = now
        row.last_changed_at = now
        if outcome is None:
            outcome = states.process(
                entity_type="bond", entity_id=str(row.id), ticker=row.ticker,
                isin=row.isin, section="bond_profile", payload=profile,
                source_url=row.kase_url or "https://kase.kz/",
                source_timestamp=stub.provenance.source_timestamp if stub.provenance else None,
                job_id=job.row.id,
            )
        if new_issuer:
            issuer_details = await sync_issuer_details(collector, code, issuer_cache[code])
            metrics["financial_statements"] += issuer_details["statements"]
            metrics["ratings"] += issuer_details["ratings"]
            documents = await provider.get_documents(code)
            collector._flush_raw()
            document_metrics = await DocumentIngestionService(session).ingest(
                entity_id=str(row.id), ticker=row.ticker, issuer_code=code,
                documents=[{
                    "url": item.url, "name": item.title, "kind": item.kind,
                    "published_at": item.published_at,
                } for item in documents],
            )
            metrics["new_documents"] += document_metrics["new_documents"]
            metrics["document_versions"] += document_metrics["new_versions"]
            metrics["documents_skipped"] += document_metrics["documents_skipped"]
            metrics["ai_tasks_created"] += document_metrics["ai_tasks_created"]
        metrics["deep_extractions"] += 1
        if existing is None:
            metrics["new_records"] += 1
        else:
            metrics["updated_records"] += 1
        metrics["entities_changed"] += 1
        metrics["ai_tasks_created"] += len(outcome.plan.ai_tasks)

    if catalog_valid:
        now = datetime.now(timezone.utc)
        for key, bond in known.items():
            if key not in seen and bond.is_active:
                bond.is_active = False
                bond.last_checked_at = now
                bond.last_changed_at = now
                current = states.latest("bond", str(bond.id), "bond_profile")
                inactive_profile = dict(current.normalized_json) if current else {"ticker": bond.ticker}
                inactive_profile["is_active"] = False
                states.process(
                    entity_type="bond", entity_id=str(bond.id), ticker=bond.ticker,
                    isin=bond.isin, section="bond_profile",
                    payload=inactive_profile,
                    source_url=bond.kase_url or "https://kase.kz/", job_id=job.row.id,
                )
                metrics["inactive_records"] += 1
                metrics["entities_changed"] += 1

    metrics.update({
        "pages_checked": metrics["entities_checked"],
        "pages_changed": metrics["entities_changed"],
        "skipped_unchanged": metrics["entities_unchanged"],
        "db_updates": metrics["new_records"] + metrics["updated_records"] + metrics["inactive_records"],
        "ai_analyses": 0,
        "ai_calls_saved": metrics["entities_unchanged"],
        "anomalies": metrics["entities_failed"],
    })
    metrics["status"] = "completed" if catalog_valid else "partial"
    tracker = job.finish(metrics, None if catalog_valid else "catalog response failed validation")
    session.commit()
    return {**metrics, "job_id": tracker.id}


__all__ = ["incremental_catalog_sync"]
