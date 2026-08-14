from __future__ import annotations

from datetime import datetime, timezone
import pytest
from sqlalchemy import func, select

from app.models.incremental import (
    AIChangeTask,
    DataChangeSet,
    DataCurrentState,
    DataStateVersion,
    DocumentVersion,
    KaseDocument,
    RecalculationTask,
    SourceCheckLog,
    IngestionJob,
)
from app.models.issuer import Issuer
from app.models.bond import Bond
from app.models.market import BondTrade
from app.repositories.market import TradeRepository
from app.services.incremental import IncrementalStateService, JobTracker, RecalculationPlanner, content_hash
from app.services.incremental_documents import DocumentIngestionService, NewsIngestionService
from app.services.ai_change_worker import run_ai_change_tasks
from app.ai.base import LLMResponse


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_unchanged_section_skips_history_recalc_and_ai(session):
    service = IncrementalStateService(session)
    payload = {"bid": 98.5, "ask": 99.2, "last": 99.0, "ytm": 0.184}
    first = service.process(
        entity_type="bond", entity_id="unchanged-1", ticker="ABCb5",
        section="quote", payload=payload, source_url="https://kase.kz/test",
    )
    before = {
        "versions": _count(session, DataStateVersion),
        "changes": _count(session, DataChangeSet),
        "recalc": _count(session, RecalculationTask),
        "ai": _count(session, AIChangeTask),
    }
    second = service.process(
        entity_type="bond", entity_id="unchanged-1", ticker="ABCb5",
        section="quote", payload=dict(reversed(list(payload.items()))),
        source_url="https://kase.kz/test",
    )
    assert first.status == "created"
    assert second.status == "unchanged"
    assert _count(session, DataStateVersion) == before["versions"]
    assert _count(session, DataChangeSet) == before["changes"]
    assert _count(session, RecalculationTask) == before["recalc"]
    assert _count(session, AIChangeTask) == before["ai"]
    assert session.scalars(select(SourceCheckLog).where(SourceCheckLog.entity_id == "unchanged-1")).all()[-1].status == "unchanged"


def test_one_field_change_only_records_that_field(session):
    service = IncrementalStateService(session)
    base = {"bid": 99.0, "ask": 100.0, "ytm": 0.18}
    service.process(entity_type="bond", entity_id="field-1", section="quote", payload=base, source_url="https://kase.kz/q")
    result = service.process(entity_type="bond", entity_id="field-1", section="quote", payload={**base, "ask": 101.0}, source_url="https://kase.kz/q")
    assert result.status == "updated"
    assert [(c["field"], c["old"], c["new"]) for c in result.changes] == [("ask", 100.0, 101.0)]
    assert "liquidity" in result.plan.scores
    assert "credit" not in result.plan.scores


def test_parser_break_is_anomaly_and_does_not_overwrite_current(session):
    service = IncrementalStateService(session)
    original = {f"field_{i}": i for i in range(10)} | {"coupon_rate": 0.18}
    service.process(entity_type="bond", entity_id="anomaly-1", section="issue_terms", payload=original, source_url="https://kase.kz/a")
    broken = {key: None for key in original}
    outcome = service.process(entity_type="bond", entity_id="anomaly-1", section="issue_terms", payload=broken, source_url="https://kase.kz/a")
    current = session.scalar(select(DataCurrentState).where(DataCurrentState.entity_id == "anomaly-1"))
    assert outcome.status == "anomaly"
    assert outcome.anomaly
    assert current.normalized_json == original
    assert _count(session, DataStateVersion) >= 1


def test_recalculation_dependency_graph_is_selective():
    plan = RecalculationPlanner().plan([{"section": "quote", "field": "ask", "material": True}])
    assert {"bid_ask_spread", "purchase_calculator"} <= plan.calculations
    assert "credit" not in plan.scores
    assert plan.ai_tasks == {"MarketChangeExplainer"}


@pytest.mark.anyio
async def test_same_document_url_creates_version_only_when_hash_changes(session, tmp_path):
    service = DocumentIngestionService(session, tmp_path)
    payload = [{"url": "https://kase.kz/files/report.pdf", "name": "report.pdf"}]
    bodies = [b"version one", b"version one", b"version two"]

    async def fetch(_url):
        return bodies.pop(0), {}

    first = await service.ingest(entity_id="doc-bond", documents=payload, ticker="DOCb1", fetch=fetch)
    second = await service.ingest(entity_id="doc-bond", documents=payload, ticker="DOCb1", fetch=fetch)
    third = await service.ingest(entity_id="doc-bond", documents=payload, ticker="DOCb1", fetch=fetch)
    doc = session.scalar(select(KaseDocument).where(KaseDocument.document_url == payload[0]["url"]))
    versions = session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_number)).all()
    assert first["new_versions"] == 1
    assert second["documents_skipped"] == 1
    assert third["new_versions"] == 1
    assert [row.version_number for row in versions] == [1, 2]
    assert versions[0].content_hash != versions[1].content_hash


def test_append_only_trades_and_repeated_job_are_idempotent(session):
    issuer = Issuer(code="TRADE-ISSUER", name="Trade issuer")
    session.add(issuer)
    session.flush()
    bond = Bond(ticker="TRDb1", issuer_id=issuer.id, name="Trade bond", currency="KZT")
    session.add(bond)
    session.flush()
    repo = TradeRepository(session)
    keys = ["1000", "1001", "1002"]
    for trade_id in keys:
        fingerprint = content_hash({"ticker": bond.ticker, "trade_id": trade_id})
        _, created = repo.add_if_new(BondTrade(
            bond_id=bond.id, trade_id=trade_id, timestamp=datetime.now(timezone.utc),
            data_mode="live", fingerprint=fingerprint,
        ))
        assert created
    duplicate, created = repo.add_if_new(BondTrade(
        bond_id=bond.id, trade_id="1002", timestamp=datetime.now(timezone.utc),
        data_mode="live", fingerprint=content_hash({"ticker": bond.ticker, "trade_id": "1002"}),
    ))
    assert not created and duplicate.trade_id == "1002"
    assert session.scalar(select(func.count()).select_from(BondTrade).where(BondTrade.bond_id == bond.id)) == 3

    first = JobTracker(session, "quotes", "repeat-job-key")
    first.finish({"entities_checked": 1})
    second = JobTracker(session, "quotes", "repeat-job-key")
    assert second.reused and second.row.id == first.row.id
    assert session.scalar(select(func.count()).select_from(IngestionJob).where(IngestionJob.idempotency_key == "repeat-job-key")) == 1


def test_news_fingerprint_deduplicates_and_only_queues_new_publication(session):
    service = NewsIngestionService(session)
    item = {"title": "Новая отчётность", "publication_date": "2026-08-14", "url": "https://kase.kz/news/1"}
    first = service.ingest(entity_id="news-bond", ticker="NEWSb1", issuer_code="NEWS", items=[item])
    second = service.ingest(entity_id="news-bond", ticker="NEWSb1", issuer_code="NEWS", items=[item])
    assert first["new_news"] == 1
    assert first["ai_tasks_created"] == 1
    assert second == {"new_news": 0, "ai_tasks_created": 0}


def test_bond_change_feed_summary_and_freshness_api(api):
    listing = api.get("/bonds?limit=1")
    assert listing.status_code == 200
    ticker = listing.json()["items"][0]["ticker"]
    changes = api.get(f"/bonds/{ticker}/changes?section=quote&importance=0&limit=10")
    summary = api.get(f"/bonds/{ticker}/change-summary")
    detail = api.get(f"/bonds/{ticker}")
    monitoring = api.get("/meta/ingestion-metrics?hours=24")
    assert changes.status_code == 200
    assert summary.status_code == 200
    assert {"changed", "material_changes", "summary"} <= summary.json().keys()
    assert detail.status_code == 200
    assert {"last_checked_at", "last_changed_at", "source_timestamp", "data_mode"} <= detail.json()["freshness"].keys()
    assert monitoring.status_code == 200
    assert {"pages_checked", "pages_changed", "AI_calls_saved", "average_check_latency_ms"} <= monitoring.json().keys()


@pytest.mark.anyio
async def test_selective_ai_worker_receives_only_change_payload(session, monkeypatch):
    started = datetime.now(timezone.utc)
    service = IncrementalStateService(session)
    service.process(
        entity_type="bond", entity_id="ai-bond", ticker="AIb1", section="quote",
        payload={"ask": 100.0, "ytm": 0.18}, source_url="https://kase.kz/ai",
    )
    captured = []

    class Client:
        async def chat(self, messages, **_kwargs):
            captured.append(messages[-1].content)
            return LLMResponse(content="Изменилась доходность.", model="test-local", provider="local")

    import app.services.ai_change_worker as worker
    monkeypatch.setattr(worker.settings, "AI_ENABLED", True)
    monkeypatch.setattr(worker.settings, "AI_PROVIDER", "local")
    monkeypatch.setattr(worker, "get_llm_client", lambda: Client())
    result = await run_ai_change_tasks(session, since=started)
    task = session.scalar(select(AIChangeTask).where(AIChangeTask.entity_id == "ai-bond"))
    assert result == {"ai_analyses": 1, "ai_failures": 0}
    assert task.status == "completed"
    assert "previous_relevant_state" in captured[0]
    assert "new_relevant_state" in captured[0]
