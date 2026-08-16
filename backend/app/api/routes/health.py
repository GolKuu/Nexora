from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.services.health_service import app_health, kase_browser_health, kase_health

router = APIRouter()


@router.get("/health", summary="Состояние приложения и БД")
def health(session: Session = Depends(get_session)) -> dict:
    return app_health(session)


@router.get(
    "/health/kase",
    summary="Реальная проверка подключения к KASE",
    description=(
        "Выполняет фактический запрос к настроенному источнику. "
        "Никогда не сообщает о подключении на основании одной лишь конфигурации; "
        "если отдаются демо-данные, в ответе будет предупреждение."
    ),
)
async def health_kase() -> dict:
    return await kase_health()


@router.get(
    "/health/kase-browser",
    summary="Реальная проверка публичной страницы KASE через браузер",
)
async def health_kase_browser() -> dict:
    return await kase_browser_health()
