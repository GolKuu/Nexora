from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Identity, get_identity, require_owner
from app.db.session import get_session
from app.schemas.settings import SettingsResponse, SettingsUpdate
from app.services.settings_service import SettingsService

router = APIRouter()


@router.get("", response_model=SettingsResponse, summary="Настройки пользователя")
def get_settings(
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
) -> SettingsResponse:
    service = SettingsService(session)
    return SettingsResponse(
        **service.get(user_id=identity.user_id, token=identity.token)
    )


@router.put("", response_model=SettingsResponse, summary="Сохранить настройки")
def update_settings(
    payload: SettingsUpdate,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> SettingsResponse:
    service = SettingsService(session)
    return SettingsResponse(
        **service.update(
            user_id=identity.user_id, token=identity.token, values=payload.changes()
        )
    )


@router.get("/inflation", summary="Какая инфляция используется и откуда взята")
def effective_inflation(
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
    horizon_years: float | None = None,
) -> dict:
    service = SettingsService(session)
    prefs = service.get(user_id=identity.user_id, token=identity.token)
    return service.effective_inflation(prefs, horizon_years)
