"""Endpoints backed by the browser agent.

These are deliberately *controlled* entry points, not a general-purpose remote
browser: every one of them either targets an instrument we know or an official
kase.kz URL, and every one returns a user-facing status rather than automation
detail (§49).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.browser.toolbox import BrowserToolbox
from app.core.config import settings
from app.db.session import get_session
from app.services.browser_agent_service import (
    BrowserAgentService,
    browser_status,
    require_browser,
)

router = APIRouter()


class VerifyRequest(BaseModel):
    sections: list[str] | None = Field(
        default=None,
        description="Какие разделы страницы открыть; по умолчанию — релевантные.",
    )
    max_tabs: int = Field(default=4, ge=1, le=10)
    with_visual: bool = Field(
        default=False,
        description="Разобрать скриншот моделью. Только качественные выводы.",
    )


class InspectRequest(BaseModel):
    url: str = Field(description="Публичный адрес на kase.kz")
    with_visual: bool = False


@router.get("/browser/status", summary="Состояние браузерного агента")
def status() -> dict:
    return {
        "configured_mode": settings.KASE_DATA_MODE,
        "website": settings.KASE_WEBSITE_URL,
        "language": settings.KASE_LANGUAGE,
        "requires_api_key": settings.KASE_DATA_MODE == "official_api",
        "commands": BrowserToolbox.__doc__.strip().splitlines()[0]
        if BrowserToolbox.__doc__
        else None,
        **browser_status(),
    }


@router.post(
    "/bonds/{identifier}/verify-on-kase",
    summary="Проверить на KASE",
    description=(
        "Открывает официальную страницу выпуска в браузере, читает видимый "
        "текст, таблицы и вкладки, сверяет значения и возвращает проверенные "
        "поля с указанием источника и времени."
    ),
)
async def verify_on_kase(
    identifier: str,
    payload: VerifyRequest = Body(default=VerifyRequest()),
    session: Session = Depends(get_session),
) -> dict:
    require_browser()
    return await BrowserAgentService(session).verify_bond(
        identifier,
        sections=payload.sections,
        max_tabs=payload.max_tabs,
        with_visual=payload.with_visual,
    )


@router.get(
    "/bonds/{identifier}/kase-tab/{section}",
    summary="Прочитать вкладку на странице KASE",
    description=(
        "Разделы определяются на самой странице; названия не захардкожены. "
        "Известные ключи: characteristics, trades, documents, payments, "
        "financials, news, history, related."
    ),
)
async def read_kase_tab(
    identifier: str,
    section: str,
    session: Session = Depends(get_session),
) -> dict:
    require_browser()
    return await BrowserAgentService(session).read_tab(identifier, section)


@router.get(
    "/bonds/{identifier}/kase-link",
    summary="Открыть на KASE",
    description="Официальный подтвержденный адрес страницы выпуска.",
)
def kase_link(identifier: str, session: Session = Depends(get_session)) -> dict:
    from app.services.bond_service import BondService

    bond = BondService(session).require(identifier)
    return {
        "ticker": bond.ticker,
        "url": bond.kase_url,
        "verified_at": bond.fetched_at.isoformat() if bond.fetched_at else None,
        "source": bond.source,
    }


@router.post(
    "/browser/catalog-refresh",
    summary="Обновить каталог облигаций с сайта KASE",
)
async def catalog_refresh(
    session: Session = Depends(get_session),
    limit: int | None = Query(default=None, ge=1, le=5000),
) -> dict:
    require_browser()
    return await BrowserAgentService(session).refresh_catalog(limit=limit)


@router.post(
    "/browser/inspect",
    summary="Прочитать произвольную публичную страницу KASE",
)
async def inspect(
    payload: InspectRequest,
    session: Session = Depends(get_session),
) -> dict:
    require_browser()
    return await BrowserAgentService(session).inspect_url(
        payload.url, with_visual=payload.with_visual
    )
