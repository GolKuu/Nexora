"""Change detection, selective work planning and transactional persistence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.incremental import (
    AIChangeTask,
    DataChangeSet,
    DataCurrentState,
    DataStateVersion,
    IngestionJob,
    RecalculationTask,
    SourceCheckLog,
)
from app.models.browser import RawBrowserSnapshot

PARSER_VERSION = settings.INCREMENTAL_PARSER_VERSION
NOISE_KEYS = {
    "session_id", "csrf", "csrf_token", "nonce", "request_id", "analytics",
    "tracking", "cookie_banner", "current_time", "rendered_at",
}
TRACKING_QUERY = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid"}
RANDOM_DOM_ID = re.compile(r"^(?:ember|react|mat|ng|radix)-?[a-f0-9_-]{6,}$", re.I)

SECTION_FIELDS: dict[str, set[str]] = {
    "bond_profile": {"ticker", "isin", "name", "currency", "bond_type", "is_active"},
    "issue_terms": {"nominal", "issue_date", "maturity_date", "coupon_rate", "coupon_type", "coupon_frequency", "day_count", "issue_size", "outstanding_amount", "secured", "subordinated", "callable", "putable"},
    "quote": {"bid", "ask", "last", "ytm", "clean_price", "dirty_price", "accrued_interest", "bid_volume", "ask_volume", "volume", "turnover", "number_of_trades"},
    "order_book": {"bid", "ask", "bid_volume", "ask_volume"},
    "trades": {"trades"},
    "cashflows": {"cashflows", "next_coupon_date"},
    "issuer": {"issuer", "issuer_code"},
    "ratings": {"ratings"},
    "news": {"news"},
    "documents": {"documents"},
    "financials": {"financials", "statements"},
}


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 10)
    return value


def normalize_url(value: str) -> str:
    parts = urlsplit(value)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_QUERY]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", urlencode(sorted(query)), ""))


def normalize_content(value: Any, *, key: str | None = None) -> Any:
    """Canonicalize structured content before hashing and comparison."""
    if key and key.lower() in NOISE_KEYS:
        return None
    if isinstance(value, dict):
        result = {}
        for item_key in sorted(value):
            lowered = str(item_key).lower()
            if lowered in NOISE_KEYS:
                continue
            normalized = normalize_content(value[item_key], key=lowered)
            if lowered in {"id", "dom_id"} and isinstance(normalized, str) and RANDOM_DOM_ID.match(normalized):
                continue
            result[str(item_key)] = normalized
        return result
    if isinstance(value, (list, tuple)):
        return [normalize_content(item) for item in value]
    if isinstance(value, str):
        text = " ".join(value.replace("\xa0", " ").split())
        return normalize_url(text) if text.startswith(("https://", "http://")) else text
    return _json_value(value)


def content_hash(value: Any) -> str:
    payload = json.dumps(normalize_content(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def section_payloads(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized = normalize_content(payload)
    sections: dict[str, dict[str, Any]] = {}
    for section, fields in SECTION_FIELDS.items():
        selected = {name: normalized[name] for name in sorted(fields) if name in normalized}
        if selected:
            sections[section] = selected
    unknown = {
        key: value for key, value in normalized.items()
        if key not in {"timestamp", "fetched_at"}
        and not any(key in fields for fields in SECTION_FIELDS.values())
    }
    if unknown:
        sections["other"] = unknown
    return sections


def field_changes(previous: Any, current: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Return changed leaf fields only; list changes stay atomic and auditable."""
    if isinstance(previous, dict) and isinstance(current, dict):
        changes = []
        for key in sorted(set(previous) | set(current)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in previous:
                changes.append({"field": path, "old": None, "new": current[key], "change_type": "created"})
            elif key not in current:
                changes.append({"field": path, "old": previous[key], "new": None, "change_type": "deleted"})
            else:
                changes.extend(field_changes(previous[key], current[key], path))
        return changes
    if previous != current:
        change_type = "restored" if previous is None and current is not None else "updated"
        return [{"field": prefix or "$", "old": previous, "new": current, "change_type": change_type}]
    return []


def suspected_anomaly(previous: Any, current: Any) -> tuple[bool, str | None]:
    if isinstance(previous, dict) and isinstance(current, dict) and previous:
        known = [key for key, value in previous.items() if value is not None]
        missing = [key for key in known if current.get(key) is None]
        if known and len(missing) / len(known) >= settings.INCREMENTAL_MISSING_FIELD_RATIO:
            return True, f"{len(missing)}/{len(known)} previously populated fields disappeared"
        for key, old in previous.items():
            new = current.get(key)
            if isinstance(old, (int, float)) and isinstance(new, (int, float)) and old:
                ratio = abs(new / old)
                if key.lower().endswith(("ytm", "rate", "yield")) and ratio >= 20:
                    return True, f"implausible {key} ratio {ratio:.1f}x"
    return False, None


class ChangeImportance:
    @staticmethod
    def score(section: str, field: str, old: Any, new: Any) -> int:
        leaf = field.rsplit(".", 1)[-1]
        if section == "documents" and old is None:
            return 75
        if section == "news" and old is None:
            return 55
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            return 45 if old != new else 0
        absolute = abs(new - old)
        relative = absolute / abs(old) if old else 1.0
        if leaf == "ytm":
            return min(100, round(20 + absolute / max(settings.MATERIAL_YTM_ABSOLUTE_CHANGE, 1e-9) * 35))
        if leaf in {"bid", "ask", "last", "clean_price"}:
            return min(100, round(15 + relative * 100 / max(settings.MATERIAL_PRICE_PERCENT_CHANGE, 0.01) * 35))
        if leaf in {"credit_score", "liquidity_score"}:
            threshold = settings.MATERIAL_CREDIT_SCORE_CHANGE if leaf == "credit_score" else settings.MATERIAL_LIQUIDITY_SCORE_CHANGE
            return min(100, round(20 + absolute / max(threshold, 0.01) * 40))
        if leaf in {"volume", "turnover", "number_of_trades"}:
            return min(100, round(10 + relative / max(settings.MATERIAL_TRADE_VOLUME_CHANGE, 0.01) * 30))
        return min(100, round(20 + relative * 40))


@dataclass(slots=True)
class WorkPlan:
    calculations: set[str] = field(default_factory=set)
    scores: set[str] = field(default_factory=set)
    ai_tasks: set[str] = field(default_factory=set)


class RecalculationPlanner:
    DEPENDENCIES = {
        "quote": WorkPlan(
            {"purchase_calculator", "bid_ask_spread", "market_metrics"},
            {"liquidity", "trade", "investment"}, {"MarketChangeExplainer"}
        ),
        "order_book": WorkPlan({"bid_ask_spread"}, {"liquidity", "trade"}, {"LiquidityAnalyst"}),
        "trades": WorkPlan({"turnover", "liquidity_metrics"}, {"liquidity", "trade", "investment"}, {"LiquidityAnalyst"}),
        "financials": WorkPlan({"issuer_metrics"}, {"credit", "investment", "hold"}, {"FinancialDocumentAnalyzer", "CreditAnalyst"}),
        "ratings": WorkPlan({"credit_metrics"}, {"credit", "investment", "hold"}, {"CreditAnalyst"}),
        "documents": WorkPlan(set(), set(), {"FinancialDocumentAnalyzer"}),
        "news": WorkPlan(set(), set(), {"NewsAnalyzer"}),
        "bond_profile": WorkPlan({"bond_metrics"}, {"investment", "hold", "trade"}, {"FullBondAnalysis"}),
        "issue_terms": WorkPlan({"cashflows", "ytm", "duration"}, {"investment", "hold", "trade"}, {"FullBondAnalysis"}),
        "cashflows": WorkPlan({"cashflows", "ytm", "duration"}, {"investment", "hold"}, {"FullBondAnalysis"}),
        "inflation": WorkPlan({"real_return"}, {"real_return", "investment"}, set()),
    }

    def plan(self, changes: list[dict[str, Any]]) -> WorkPlan:
        result = WorkPlan()
        for change in changes:
            dependency = self.DEPENDENCIES.get(change["section"], WorkPlan())
            result.calculations.update(dependency.calculations)
            result.scores.update(dependency.scores)
            if change.get("material"):
                result.ai_tasks.update(dependency.ai_tasks)
        return result


@dataclass(slots=True)
class ProcessResult:
    status: str
    section: str
    current_state_id: int | None = None
    version_id: int | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    plan: WorkPlan = field(default_factory=WorkPlan)
    anomaly: str | None = None


class IncrementalStateService:
    def __init__(self, session: Session):
        self.session = session
        self.planner = RecalculationPlanner()

    def latest(self, entity_type: str, entity_id: str, section: str) -> DataCurrentState | None:
        return self.session.execute(select(DataCurrentState).where(
            DataCurrentState.entity_type == entity_type,
            DataCurrentState.entity_id == entity_id,
            DataCurrentState.section == section,
        )).scalar_one_or_none()

    def process(
        self, *, entity_type: str, entity_id: str, section: str, payload: dict | list,
        source_url: str, ticker: str | None = None, isin: str | None = None,
        source_timestamp: datetime | None = None, etag: str | None = None,
        last_modified: str | None = None, snapshot_id: int | None = None,
        parser_version: str = PARSER_VERSION, checked_at: datetime | None = None,
        job_id: int | None = None, validated_missing: bool = False,
        enqueue_tasks: bool = True,
    ) -> ProcessResult:
        now = checked_at or datetime.now(timezone.utc)
        normalized = normalize_content(payload)
        digest = content_hash(normalized)
        current = self.latest(entity_type, entity_id, section)

        if current and current.content_hash == digest:
            current.last_checked_at = now
            current.parser_version = parser_version
            current.source_timestamp = source_timestamp or current.source_timestamp
            current.etag = etag or current.etag
            current.last_modified = last_modified or current.last_modified
            self._check_log(source_url, entity_id, section, now, "unchanged", False, digest, etag, last_modified, job_id=job_id)
            self.session.flush()
            return ProcessResult("unchanged", section, current.id)

        previous = current.normalized_json if current else {}
        anomaly, reason = suspected_anomaly(previous, normalized)
        if (
            not anomaly and not validated_missing
            and isinstance(previous, dict) and isinstance(normalized, dict)
        ):
            vanished = [
                key for key, value in previous.items()
                if value is not None and (key not in normalized or normalized.get(key) is None)
            ]
            if vanished:
                anomaly = True
                reason = f"previously populated fields disappeared without validation: {', '.join(vanished[:8])}"
        if anomaly:
            self._check_log(source_url, entity_id, section, now, "anomaly", False, digest, etag, last_modified, error=reason, job_id=job_id)
            self.session.flush()
            return ProcessResult("anomaly", section, current.id if current else None, anomaly=reason)

        if current is None:
            current = DataCurrentState(
                entity_type=entity_type, entity_id=entity_id, ticker=ticker, isin=isin,
                section=section, source_url=source_url, source_timestamp=source_timestamp,
                last_checked_at=now, last_changed_at=now, etag=etag, last_modified=last_modified,
                content_hash=digest, normalized_json=normalized, parser_version=parser_version,
                snapshot_id=snapshot_id,
            )
            self.session.add(current)
            self.session.flush()
            raw_changes = [{"field": key, "old": None, "new": value, "change_type": "created"} for key, value in (normalized.items() if isinstance(normalized, dict) else [("$", normalized)])]
            status = "created"
            previous_version = None
        else:
            previous_version = self.session.execute(select(DataStateVersion).where(
                DataStateVersion.current_state_id == current.id
            ).order_by(desc(DataStateVersion.detected_at)).limit(1)).scalar_one_or_none()
            raw_changes = field_changes(previous, normalized)
            current.ticker = ticker or current.ticker
            current.isin = isin or current.isin
            current.source_url = source_url
            current.source_timestamp = source_timestamp
            current.last_checked_at = now
            current.last_changed_at = now
            current.etag = etag
            current.last_modified = last_modified
            current.content_hash = digest
            current.normalized_json = normalized
            current.parser_version = parser_version
            current.snapshot_id = snapshot_id
            status = "updated"

        version = DataStateVersion(
            current_state_id=current.id, entity_type=entity_type, entity_id=entity_id,
            section=section, content_hash=digest, normalized_json=normalized,
            detected_at=now, source_timestamp=source_timestamp, snapshot_id=snapshot_id,
            parser_version=parser_version,
        )
        self.session.add(version)
        self.session.flush()

        changes = []
        for change in raw_changes:
            importance = ChangeImportance.score(section, change["field"], change["old"], change["new"])
            # A first baseline is material even when a string field has no
            # numeric delta: new instruments require one initial analysis.
            material = importance >= 50 or status == "created"
            fingerprint = content_hash({
                "entity_type": entity_type, "entity_id": entity_id, "section": section,
                "field": change["field"], "old": change["old"], "new": change["new"],
                "after": version.id,
            })
            row = DataChangeSet(
                entity_type=entity_type, entity_id=entity_id, ticker=ticker, isin=isin,
                section=section, field=change["field"], old_value=change["old"],
                new_value=change["new"], change_type=change["change_type"],
                source_url=source_url, source_timestamp=source_timestamp, detected_at=now,
                snapshot_before_id=previous_version.id if previous_version else None,
                snapshot_after_id=version.id, parser_version=parser_version,
                importance=importance, material=material, suspected_anomaly=False,
                change_fingerprint=fingerprint,
            )
            self.session.add(row)
            changes.append({**change, "section": section, "importance": importance, "material": material})

        plan = self.planner.plan(changes)
        if enqueue_tasks:
            self._queue(entity_type, entity_id, ticker, version.id, plan, changes, previous, normalized)
        self._check_log(source_url, entity_id, section, now, status, True, digest, etag, last_modified, job_id=job_id)
        self.session.flush()
        return ProcessResult(status, section, current.id, version.id, changes, plan)

    def _queue(
        self, entity_type: str, entity_id: str, ticker: str | None,
        version_id: int, plan: WorkPlan, changes: list[dict],
        previous: dict | list, current: dict | list,
    ) -> None:
        for task in sorted(plan.calculations | plan.scores):
            key = content_hash({"entity": entity_id, "version": version_id, "task": task})
            self.session.add(RecalculationTask(entity_type=entity_type, entity_id=entity_id, task_type=task, reason="source_change", dedupe_key=key))
        material_changes = [change for change in changes if change["material"]]
        for task in sorted(plan.ai_tasks):
            key = content_hash({"entity": entity_id, "version": version_id, "ai": task})
            self.session.add(AIChangeTask(
                entity_type=entity_type, entity_id=entity_id, ticker=ticker, task_type=task,
                payload_json={
                    "previous_relevant_state": previous,
                    "changes": material_changes,
                    "new_relevant_state": current,
                },
                model_version=settings.KASE_AI_MODEL_VERSION, dedupe_key=key,
            ))

    def _check_log(self, source_url: str, entity_id: str | None, section: str | None, checked_at: datetime, status: str, changed: bool, digest: str | None, etag: str | None, last_modified: str | None, *, http_status: int | None = None, latency_ms: float | None = None, error: str | None = None, job_id: int | None = None) -> None:
        self.session.add(SourceCheckLog(
            source_url=source_url, entity_id=entity_id, section=section, checked_at=checked_at,
            status=status, http_status=http_status, latency_ms=latency_ms, etag=etag,
            last_modified=last_modified, content_hash=digest, changed=changed, error=error,
            job_id=job_id,
        ))


class JobTracker:
    def __init__(self, session: Session, job_type: str, idempotency_key: str):
        self.session = session
        self.row = session.execute(select(IngestionJob).where(IngestionJob.idempotency_key == idempotency_key)).scalar_one_or_none()
        self.reused = self.row is not None
        if self.row is None:
            self.row = IngestionJob(idempotency_key=idempotency_key, job_type=job_type, started_at=datetime.now(timezone.utc), status="running")
            session.add(self.row)
            session.flush()

    def finish(self, metrics: dict[str, Any], error: str | None = None) -> IngestionJob:
        self.row.finished_at = datetime.now(timezone.utc)
        self.row.status = "failed" if error else "completed"
        for key in ("entities_checked", "entities_changed", "entities_unchanged", "entities_failed", "new_records", "updated_records", "ai_tasks_created"):
            setattr(self.row, key, int(metrics.get(key, 0)))
        self.row.metrics_json = metrics
        self.row.error_summary = error
        self.session.flush()
        return self.row


class RawSnapshotReprocessor:
    """Re-run a new parser version from stored extraction without contacting KASE."""

    def __init__(self, session: Session):
        self.session = session

    def reprocess(
        self, snapshot_id: int, *, entity_type: str, entity_id: str,
        ticker: str | None = None, isin: str | None = None,
        parser_version: str = PARSER_VERSION,
    ) -> list[ProcessResult]:
        snapshot = self.session.get(RawBrowserSnapshot, snapshot_id)
        if snapshot is None or not isinstance(snapshot.extracted_json, dict):
            raise ValueError(f"raw browser snapshot {snapshot_id} is unavailable")
        page = snapshot.extracted_json
        values = {
            name: item.get("normalized_value") if isinstance(item, dict) else item
            for name, item in (page.get("values") or {}).items()
        }
        values["documents"] = page.get("documents") or []
        values["cashflows"] = [tab for tab in (page.get("tabs_read") or []) if tab.get("section") == "payments"]
        values["financials"] = [tab for tab in (page.get("tabs_read") or []) if tab.get("section") == "financials"]
        values["news"] = [tab for tab in (page.get("tabs_read") or []) if tab.get("section") == "news"]
        service = IncrementalStateService(self.session)
        return [
            service.process(
                entity_type=entity_type, entity_id=entity_id, ticker=ticker,
                isin=isin, section=section, payload=payload,
                source_url=snapshot.url, source_timestamp=snapshot.fetched_at,
                snapshot_id=snapshot.id, parser_version=parser_version,
            )
            for section, payload in section_payloads(values).items()
        ]


__all__ = [
    "ChangeImportance", "IncrementalStateService", "JobTracker", "ProcessResult",
    "RawSnapshotReprocessor", "RecalculationPlanner", "WorkPlan", "content_hash", "field_changes",
    "normalize_content", "section_payloads", "suspected_anomaly",
]
