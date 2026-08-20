"""Honest health reporting.

``/health/kase`` performs a real probe. It never reports "connected" on the
strength of configuration alone, and it always states whether the data being
served is real or demo.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

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
    "offline_cache": "cache",
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


_browser_probe_state: dict[str, object | None] = {
    "last_attempt": None,
    "last_success": None,
    "latency_ms": None,
    "last_error": None,
}


async def _probe_kase_browser() -> dict:
    """Open the public KASE home page in Chromium and report what happened."""
    from app.browser.agent import KaseBrowsingContext

    async with KaseBrowsingContext(label="health:kase-browser") as agent:
        navigation = await agent._goto(agent.url_for("home"), min_chars=200)
        current_url = await agent.session.get_current_url()
        return {
            **navigation,
            "url": current_url,
            "domain_confirmed": agent.confirms_domain(current_url),
        }


async def kase_browser_health() -> dict:
    """Health is green only after a real anonymous browser navigation."""
    attempted_at = datetime.now(timezone.utc)
    _browser_probe_state["last_attempt"] = attempted_at.isoformat()
    started = time.perf_counter()

    if not settings.BROWSER_ENABLED:
        _browser_probe_state["last_error"] = "BROWSER_ENABLED=false"
        return {
            "connected": False,
            **_browser_probe_state,
            "browser_status": "disabled",
        }

    try:
        result = await _probe_kase_browser()
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        connected = bool(result.get("ok") and result.get("domain_confirmed"))
        status = str(result.get("status") or ("ok" if connected else "error"))
        error = None if connected else str(result.get("error") or status)
        _browser_probe_state["latency_ms"] = latency_ms
        _browser_probe_state["last_error"] = error
        if connected:
            _browser_probe_state["last_success"] = attempted_at.isoformat()
        return {
            "connected": connected,
            **_browser_probe_state,
            "browser_status": status,
            "last_error": error,
            "url": result.get("url"),
            "browser_blocked_by_captcha": bool(
                result.get("browser_blocked_by_captcha")
            ),
            "requires_authentication": bool(result.get("requires_authentication")),
        }
    except Exception as exc:
        _browser_probe_state["latency_ms"] = round(
            (time.perf_counter() - started) * 1000, 1
        )
        _browser_probe_state["last_error"] = str(exc)
        return {
            "connected": False,
            **_browser_probe_state,
            "browser_status": "error",
            "last_error": str(exc),
        }


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


#: How many recent cycles the average latency is taken over. Enough to smooth a
#: single slow pass, short enough to still describe the present.
MONITORING_WINDOW = 20


def monitoring_health(session: Session) -> dict:
    """Whether the ten-minute loop is actually running, from stored evidence.

    Every number here comes from ``monitoring_cycles`` rows written by the loop
    itself, so a configured-but-dead scheduler reports ``never_run`` or
    ``stalled`` rather than looking healthy because the setting is present.

    A cycle that changed nothing is healthy: unchanged data is deliberately not
    re-written (§9), so ``instruments_changed: 0`` is the normal quiet case.
    """
    from app.models.history import IngestionAnomaly, MonitoringCycle

    now = datetime.now(timezone.utc)
    interval = settings.MONITORING_INTERVAL_SECONDS
    recent = list(
        session.execute(
            select(MonitoringCycle)
            .where(MonitoringCycle.job_type == "monitoring")
            .order_by(MonitoringCycle.started_at.desc())
            .limit(MONITORING_WINDOW)
        ).scalars()
    )
    last = recent[0] if recent else None
    last_success = next((row for row in recent if row.status == "ok"), None)

    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    finished = _aware(last.finished_at) if last else None
    next_cycle = finished + timedelta(seconds=interval) if finished else None
    age_seconds = (now - finished).total_seconds() if finished else None

    if last is None:
        status = "never_run"
    elif age_seconds is not None and age_seconds > interval * 3:
        # Three missed cycles is no longer a slow pass, it is a stopped loop.
        status = "stalled"
    elif last.status != "ok":
        status = "degraded"
    else:
        status = "ok"

    latencies = [row.duration_ms for row in recent if row.duration_ms is not None]
    unresolved = int(
        session.execute(
            select(func.count(IngestionAnomaly.id)).where(
                IngestionAnomaly.job_type == "monitoring",
                IngestionAnomaly.resolved.is_(False),
            )
        ).scalar_one()
        or 0
    )

    return {
        "status": status,
        "enabled": settings.INCREMENTAL_ENABLED and not settings.is_serverless,
        "interval_seconds": interval,
        "schedule": f"*/{max(interval // 60, 1)} * * * *",
        "server_side": True,
        "last_cycle_at": finished.isoformat() if finished else None,
        "last_successful_cycle_at": (
            _aware(last_success.finished_at).isoformat() if last_success else None
        ),
        "next_cycle_at": next_cycle.isoformat() if next_cycle else None,
        "seconds_since_last_cycle": None if age_seconds is None else round(age_seconds, 1),
        "instruments_checked": last.instruments_checked if last else 0,
        "instruments_changed": last.instruments_changed if last else 0,
        "observations_created": last.observations_created if last else 0,
        "duplicates_skipped": last.duplicates if last else 0,
        "failures": last.failures if last else 0,
        "parser_anomalies": last.anomalies if last else 0,
        "unresolved_parser_anomalies": unresolved,
        "market_day": last.market_day if last else None,
        "last_error": last.error if last else None,
        "cycles_observed": len(recent),
        "average_latency_ms": (
            round(sum(latencies) / len(latencies), 1) if latencies else None
        ),
        "failures_in_window": sum(row.failures for row in recent),
        "checked_at": now.isoformat(),
        # A ten-minute public-web refresh is not a real-time feed, and the
        # health endpoint is the last place that should blur the difference.
        "data_mode": DataMode.DELAYED.value,
    }
