"""Honest health reporting.

``/health/kase`` performs a real probe. It never reports "connected" on the
strength of configuration alone, and it always states whether the data being
served is real or demo.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import DataMode
from app.models.bond import Bond
from app.models.market import BondQuote
from app.providers.factory import get_provider


def database_health(session: Session) -> dict:
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    bonds = session.execute(select(func.count(Bond.id))).scalar_one()
    quotes = session.execute(select(func.count(BondQuote.id))).scalar_one()
    newest = session.execute(select(func.max(BondQuote.timestamp))).scalar_one()
    modes = session.execute(
        select(BondQuote.data_mode, func.count(BondQuote.id)).group_by(BondQuote.data_mode)
    ).all()
    return {
        "ok": True,
        "bonds": int(bonds),
        "quotes": int(quotes),
        "latest_quote_at": newest.isoformat() if newest else None,
        "quote_data_modes": {mode: int(count) for mode, count in modes},
    }


async def kase_health() -> dict:
    provider = get_provider()
    try:
        status = await provider.health()
    except Exception as exc:
        return {
            "configured_mode": settings.KASE_DATA_MODE,
            "provider": provider.name,
            "connected": False,
            "is_mock": provider.is_mock,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "detail": f"Проверка не выполнена: {exc}",
            "warning": _warning(provider.is_mock),
        }
    return {
        "configured_mode": settings.KASE_DATA_MODE,
        "provider": status.name,
        "connected": status.reachable and not status.is_mock,
        "reachable": status.reachable,
        "is_mock": status.is_mock,
        "data_mode": status.data_mode,
        "latency_ms": status.latency_ms,
        "checked_at": status.checked_at.isoformat(),
        "detail": status.detail,
        "warning": _warning(status.is_mock),
        "sub_statuses": [
            {
                "name": s.name,
                "reachable": s.reachable,
                "is_mock": s.is_mock,
                "detail": s.detail,
                "latency_ms": s.latency_ms,
            }
            for s in getattr(provider, "sub_statuses", [])
        ],
    }


def _warning(is_mock: bool) -> str | None:
    if not is_mock:
        return None
    return (
        "ВНИМАНИЕ: обслуживаются демонстрационные данные. "
        "KASE не подключен, цифры синтетические."
    )


def app_health(session: Session) -> dict:
    problems = settings.validate_runtime()
    return {
        "status": "ok" if not problems else "misconfigured",
        "app_env": settings.APP_ENV,
        "version": "0.1.0",
        "kase_data_mode": settings.KASE_DATA_MODE,
        "mock_allowed": settings.mock_allowed,
        "scoring_model_version": settings.SCORING_MODEL_VERSION,
        "formula_version": settings.FORMULA_VERSION,
        "database": database_health(session),
        "problems": problems,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "data_mode_values": [m.value for m in DataMode],
    }
