from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.schemas.bonds import CompareRequest
from app.services.compare_service import CompareService

router = APIRouter()


@router.post("/compare", summary="Сравнение выпусков")
def compare(payload: CompareRequest, session: Session = Depends(get_session)) -> dict:
    return CompareService(session).compare(
        payload.identifiers,
        mode=payload.mode,
        amount=payload.amount,
        inflation_enabled=payload.inflation_enabled,
    )
