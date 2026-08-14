"""Metadata endpoints: what the scoring model is and which categories exist.

The frontend reads weights and labels from here so that it never carries its
own copy of the model.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import BondType, DataMode, ScoreKind
from app.db.session import get_session
from app.repositories.sources import DataSourceRepository
from app.scoring.weights import COMPONENT_LABELS, get_weights
from app.models.incremental import IngestionJob, SourceCheckLog

router = APIRouter()

_SCORE_MEANINGS = {
    "investment": "Общая объективная привлекательность выпуска.",
    "credit": "Качество эмитента: способность платить.",
    "liquidity": "Насколько легко купить и продать.",
    "growth": "Потенциал роста рыночной цены, а не обещание роста.",
    "income": "Качество денежных выплат.",
    "real_return": "Привлекательность после инфляции.",
    "risk_reward": "Доходность относительно принимаемого риска.",
    "stability": "Устойчивость цены и рейтинга.",
    "exit": "Реалистичность выхода до погашения.",
    "relative_value": "Выгода относительно похожих выпусков.",
    "data_quality": "Полнота и свежесть исходных данных.",
    "analysis_confidence": "Насколько можно доверять оценке при таких данных.",
    "hold": "Насколько интересно держать до погашения.",
    "trade": "Насколько интересно выйти раньше срока.",
    "personal": "Персональная оценка пользователя; на базовые оценки не влияет.",
}

_CATEGORY_LABELS = {
    BondType.GOVERNMENT.value: "Государственные",
    BondType.QUASI_SOVEREIGN.value: "Квазигосударственные",
    BondType.MUNICIPAL.value: "Муниципальные",
    BondType.BANK.value: "Банковские",
    BondType.CORPORATE.value: "Корпоративные",
    BondType.INTERNATIONAL.value: "Международные",
}


@router.get("/meta/scoring-model", summary="Веса и версия модели оценки")
def scoring_model(profile: str = Query(default="balanced")) -> dict:
    weights = get_weights(profile)
    return {
        "version": weights.version,
        "profile": weights.profile,
        "weights": {
            "investment": weights.investment,
            "credit_corporate": weights.credit_corporate,
            "credit_bank": weights.credit_bank,
            "liquidity": weights.liquidity,
            "income": weights.income,
            "growth": weights.growth,
            "stability": weights.stability,
            "hold": weights.hold,
            "trade": weights.trade,
            "data_quality": weights.data_quality,
        },
        "labels": COMPONENT_LABELS,
        "score_kinds": [
            {"kind": k.value, "meaning": _SCORE_MEANINGS.get(k.value)} for k in ScoreKind
        ],
    }


@router.get("/meta/categories", summary="Категории выпусков для главной страницы")
def categories() -> dict:
    return {
        "categories": [
            {"code": code, "label": label} for code, label in _CATEGORY_LABELS.items()
        ],
        "data_modes": [m.value for m in DataMode],
    }


@router.get("/meta/sources", summary="Источники данных и их состояние")
def sources(session: Session = Depends(get_session)) -> dict:
    rows = DataSourceRepository(session).list()
    return {
        "configured_mode": settings.KASE_DATA_MODE,
        "app_env": settings.APP_ENV,
        "mock_allowed": settings.mock_allowed,
        "sources": [
            {
                "code": s.code,
                "name": s.name,
                "kind": s.kind,
                "is_enabled": s.is_enabled,
                "is_authoritative": s.is_authoritative,
                "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
                "last_failure_at": s.last_failure_at.isoformat() if s.last_failure_at else None,
                "last_error": s.last_error,
            }
            for s in rows
        ],
    }


@router.get("/meta/ingestion-metrics", summary="Метрики инкрементального сбора")
def ingestion_metrics(
    hours: int = Query(default=24, ge=1, le=24 * 90),
    session: Session = Depends(get_session),
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    checked, changed, latency = session.execute(select(
        func.count(SourceCheckLog.id),
        func.sum(func.cast(SourceCheckLog.changed, Integer)),
        func.avg(SourceCheckLog.latency_ms),
    ).where(SourceCheckLog.checked_at >= since)).one()
    logs = session.scalars(select(SourceCheckLog).where(SourceCheckLog.checked_at >= since)).all()
    jobs = session.scalars(select(IngestionJob).where(IngestionJob.started_at >= since)).all()
    job_metrics = [row.metrics_json or {} for row in jobs]
    checked = int(checked or 0)
    changed = int(changed or 0)
    return {
        "since": since.isoformat(), "pages_checked": checked, "pages_changed": changed,
        "change_rate": (changed / checked) if checked else 0.0,
        "deep_extractions": sum(int(m.get("deep_extractions", 0)) for m in job_metrics),
        "skipped_unchanged": sum(1 for row in logs if row.status == "unchanged"),
        "AI_calls_saved": sum(int(m.get("ai_calls_saved", 0)) for m in job_metrics),
        "documents_skipped": sum(int(m.get("documents_skipped", 0)) for m in job_metrics),
        "parser_failures": sum(1 for row in logs if row.status == "error"),
        "anomalies": sum(1 for row in logs if row.status == "anomaly"),
        "average_check_latency_ms": float(latency) if latency is not None else None,
    }
