"""Refresh jobs, callable from the scheduler, the CLI or an admin endpoint."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select

from app.collectors.kase_collector import KaseCollector, full_sync
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.providers.factory import get_provider
from app.collectors.incremental_catalog import incremental_catalog_sync
from app.collectors.kase_stock_catalog import KaseStockCatalogCollector
from app.services.change_alerts import ChangeAlertEngine
from app.services.incremental import JobTracker
from app.services.ingestion_priority import prioritized_tickers
from app.services.incremental_documents import DocumentIngestionService
from app.repositories.bonds import BondRepository
from app.services.ai_change_worker import run_ai_change_tasks
from app.models.incremental import AIChangeTask

logger = get_logger(__name__)


async def refresh_all() -> dict:
    """Reference data, quotes, trades, metrics and scores."""
    session = SessionLocal()
    try:
        summary = await full_sync(session, get_provider())
        summary["stocks"] = await KaseStockCatalogCollector(session).collect()
        logger.info("full refresh finished: %s", summary)
        return summary
    finally:
        session.close()


async def refresh_catalog_via_browser(limit: int | None = None) -> dict:
    """Sweep the public catalogue with the browser agent (§28).

    This is the periodic path: the browser refreshes the database, and user
    requests read the database. Browsing on every page view would be both slow
    and rude to the exchange.
    """
    from app.services.browser_agent_service import BrowserAgentService

    session = SessionLocal()
    try:
        result = await BrowserAgentService(session).refresh_catalog(limit=limit)
        logger.info("browser catalogue sweep: %s", result)
        return result
    finally:
        session.close()


async def refresh_catalog_incremental(*, force: bool = False, idempotency_key: str | None = None) -> dict:
    session = SessionLocal()
    try:
        result = await incremental_catalog_sync(
            session, get_provider(), force=force, idempotency_key=idempotency_key
        )
        result["stocks"] = await KaseStockCatalogCollector(session).collect()
        return result
    finally:
        session.close()


async def refresh_stocks() -> dict:
    session = SessionLocal()
    try:
        result = await KaseStockCatalogCollector(session).collect()
        from app.models.stock import Stock
        from app.services.stock_service import StockService
        service = StockService(session)
        for stock in session.execute(select(Stock)).scalars():
            service.persist_metrics_and_scores(stock)
        session.commit()
        return result
    finally:
        session.close()


async def refresh_quotes(*, idempotency_key: str | None = None) -> dict:
    """Quotes plus the derived metrics and scores that depend on them."""
    session = SessionLocal()
    try:
        job = JobTracker(session, "quotes", idempotency_key or f"quotes:{uuid4().hex}")
        if job.reused and job.row.status == "completed":
            return {**(job.row.metrics_json or {}), "status": "already_completed", "job_id": job.row.id}
        collector = KaseCollector(session, get_provider())
        result = await collector.sync_quotes(prioritized_tickers(session))
        result.update(collector.recompute_changed(result.get("changed_tickers") or []))
        result["ai_calls_saved"] = result.get("unchanged", 0)
        result["ai_tasks_created"] = int(session.scalar(
            select(func.count()).select_from(AIChangeTask).where(
                AIChangeTask.created_at >= job.row.started_at
            )
        ) or 0)
        result.update({
            "pages_checked": result.get("changed", 0) + result.get("unchanged", 0) + result.get("anomalies", 0),
            "pages_changed": result.get("changed", 0),
            "skipped_unchanged": result.get("unchanged", 0),
            "deep_extractions": 0,
            "db_updates": result.get("changed", 0),
            "ai_analyses": 0,
        })
        result["alerts_triggered"] = ChangeAlertEngine(session).evaluate_since(job.row.started_at)
        tracker = job.finish({
            **result,
            "entities_checked": result["pages_checked"],
            "entities_changed": result["pages_changed"],
            "entities_unchanged": result["skipped_unchanged"],
            "entities_failed": result.get("anomalies", 0),
            "updated_records": result["db_updates"],
            "ai_tasks_created": result["ai_tasks_created"],
        })
        session.commit()
        return {**result, "status": "completed", "job_id": tracker.id}
    finally:
        session.close()


async def refresh_documents(*, idempotency_key: str | None = None) -> dict:
    """Check issuer document listings and download only new file versions."""
    session = SessionLocal()
    try:
        job = JobTracker(session, "documents", idempotency_key or f"documents:{uuid4().hex}")
        if job.reused and job.row.status == "completed":
            return {**(job.row.metrics_json or {}), "status": "already_completed", "job_id": job.row.id}
        provider = get_provider()
        bonds = BondRepository(session)
        seen_issuers: set[int] = set()
        metrics = {"pages_checked": 0, "pages_changed": 0, "documents_skipped": 0,
                   "new_documents": 0, "new_versions": 0, "ai_tasks_created": 0,
                   "anomalies": 0, "deep_extractions": 0}
        for ticker in prioritized_tickers(session):
            bond = bonds.get_by_ticker(ticker)
            if bond is None or bond.issuer_id in seen_issuers:
                continue
            seen_issuers.add(bond.issuer_id)
            try:
                items = await provider.get_documents(bond.issuer.code)
                outcome = await DocumentIngestionService(session).ingest(
                    entity_id=str(bond.id), ticker=bond.ticker, issuer_code=bond.issuer.code,
                    documents=[{"url": item.url, "name": item.title, "kind": item.kind,
                                "published_at": item.published_at} for item in items],
                )
            except Exception as exc:
                logger.warning("document check failed for %s: %s", bond.issuer.code, exc)
                metrics["anomalies"] += 1
                continue
            metrics["pages_checked"] += 1
            metrics["new_documents"] += outcome["new_documents"]
            metrics["new_versions"] += outcome["new_versions"]
            metrics["documents_skipped"] += outcome["documents_skipped"]
            metrics["ai_tasks_created"] += outcome["ai_tasks_created"]
            metrics["pages_changed"] += int(outcome["new_versions"] > 0)
        metrics["skipped_unchanged"] = metrics["pages_checked"] - metrics["pages_changed"]
        metrics["db_updates"] = metrics["new_documents"] + metrics["new_versions"]
        metrics["ai_analyses"] = 0
        metrics["ai_calls_saved"] = metrics["documents_skipped"]
        row = job.finish({
            **metrics, "entities_checked": metrics["pages_checked"],
            "entities_changed": metrics["pages_changed"],
            "entities_unchanged": metrics["skipped_unchanged"],
            "entities_failed": metrics["anomalies"],
            "new_records": metrics["new_documents"],
            "updated_records": metrics["new_versions"],
        })
        session.commit()
        return {**metrics, "status": "completed", "job_id": row.id}
    finally:
        session.close()


async def refresh_ai_changes(*, limit: int = 50) -> dict:
    """Consume only queued material changes with the self-hosted model."""
    session = SessionLocal()
    try:
        result = await run_ai_change_tasks(session, limit=limit)
        session.commit()
        return result
    finally:
        session.close()
