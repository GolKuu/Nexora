from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import Identity, get_identity, require_admin
from app.db.session import get_session
from app.services.dcf_service import DCFAccessService, DCFService, dcf_health

router = APIRouter()


class DCFRequest(BaseModel):
    force_refresh: bool = False


@router.post("/stocks/{identifier}/dcf")
def analyze_dcf(identifier: str, payload: DCFRequest, session: Session = Depends(get_session), identity: Identity = Depends(get_identity)) -> dict:
    return DCFService(session).analyze(identifier, identity, payload.force_refresh)


@router.get("/stocks/{identifier}/dcf-history")
def dcf_history(identifier: str, session: Session = Depends(get_session), identity: Identity = Depends(get_identity)) -> dict:
    return DCFService(session).history(identifier, identity)


@router.get("/dcf/{run_id}")
def dcf_result(run_id: int, session: Session = Depends(get_session), identity: Identity = Depends(get_identity)) -> dict:
    return DCFService(session).get(run_id, identity)


@router.get("/me/dcf-usage")
def dcf_usage(session: Session = Depends(get_session), identity: Identity = Depends(get_identity)) -> dict:
    return DCFAccessService(session).usage(identity)


@router.get("/admin/dcf/{run_id}")
def dcf_audit(run_id: int, session: Session = Depends(get_session), _: bool = Depends(require_admin)) -> dict:
    return DCFService(session).audit(run_id)


@router.get("/health/dcf")
def health_dcf(session: Session = Depends(get_session)) -> dict:
    return dcf_health(session)
