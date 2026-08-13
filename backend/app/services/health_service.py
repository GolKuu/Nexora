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


#: Maps a provider name onto the coarse mode /health/kase reports (§7).
_MODE_BY_PROVIDER = {
    "kase_api": "official_api",
    "kase_public_api": "public_api",
    "kase_browser": "website",
    "kase_website": "website",
    "mock": "cache",
}


async def kase_health() -> dict:
    """Probe the configured source for real and report what came back (§7).

    ``connected`` is only ever true after a request actually succeeded against
    a non-demo source. Configuration alone never sets it.
    """
    provider = get_provider()
    attempted_at = datetime.now(timezone.utc)
    try:
        status = await provider.health()
    except Exception as exc:
        return {
            "configured_mode": settings.KASE_DATA_MODE,
            "provider": provider.name,
            "connected": False,
            "mode": _MODE_BY_PROVIDER.get(provider.name),
            "is_mock": provider.is_mock,
            "last_attempt": attempted_at.isoformat(),
            "last_success": None,
            "latency_ms": None,
            "data_age_seconds": _data_age_seconds(),
            "error": str(exc),
            "checked_at": attempted_at.isoformat(),
            "detail": f"Проверка не выполнена: {exc}",
            "warning": _warning(provider.is_mock),
        }
    connected = status.reachable and not status.is_mock
    return {
        "configured_mode": settings.KASE_DATA_MODE,
        "provider": status.name,
        "connected": connected,
        "mode": _MODE_BY_PROVIDER.get(status.name),
        "reachable": status.reachable,
        "is_mock": status.is_mock,
        "data_mode": status.data_mode,
        "last_attempt": attempted_at.isoformat(),
        "last_success": status.checked_at.isoformat() if connected else None,
        "latency_ms": status.latency_ms,
        "data_age_seconds": _data_age_seconds(),
        "error": None if status.reachable else status.detail,
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


def _data_age_seconds() -> float | None:
    """Age of the newest stored quote - how stale the served data really is.

    Read on its own short-lived session so the health probe stays usable even
    when it is called outside a request.
    """
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as session:
            newest = session.execute(select(func.max(BondQuote.timestamp))).scalar_one()
    except Exception:
        return None
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - newest).total_seconds(), 1)


def _warning(is_mock: bool) -> str | None:
    if not is_mock:
        return None
    return (
        "ВНИМАНИЕ: обслуживаются демонстрационные данные. "
        "KASE не подключен, цифры синтетические."
    )


def _browser_health() -> dict:
    """Whether the browser agent could run, without actually launching it."""
    from app.services.browser_agent_service import browser_status

    try:
        return browser_status()
    except Exception as exc:  # a missing engine is a status, not a 500
        return {"enabled": settings.BROWSER_ENABLED, "running": False, "error": str(exc)}


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
        "browser": _browser_health(),
        "problems": problems,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "data_mode_values": [m.value for m in DataMode],
    }
