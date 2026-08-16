from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Identity, get_identity, require_owner
from app.core.errors import NotFoundError
from app.db.session import get_session
from app.repositories.portfolios import AlertRepository
from app.schemas.portfolios import AlertCreate, AlertUpdate
from app.services.bond_service import BondService
from app.services.stock_service import StockService

router = APIRouter()


def _serialize(row) -> dict:
    ticker = row.stock.instrument.ticker if row.stock_id is not None else row.bond.ticker
    return {"id": row.id, "ticker": ticker, "instrument_type": row.instrument_type, "kind": row.kind,
            "threshold": row.threshold, "is_active": row.is_active,
            "last_triggered_at": row.last_triggered_at, "message": row.message}


@router.get("")
def list_alerts(session: Session = Depends(get_session), identity: Identity = Depends(get_identity)) -> dict:
    rows = AlertRepository(session).list_for_owner(user_id=identity.user_id, token=identity.token)
    return {"items": [_serialize(row) for row in rows], "requires_identity": not identity.has_owner}


@router.post("", status_code=201)
def create_alert(payload: AlertCreate, session: Session = Depends(get_session), identity: Identity = Depends(require_owner)) -> dict:
    values = {"user_id": identity.user_id, "anonymous_token": None if identity.user_id else identity.token,
              "instrument_type": payload.instrument_type, "kind": payload.kind, "threshold": payload.threshold,
              "is_active": True}
    if payload.stock is not None:
        stock = StockService(session).require(payload.stock)
        values.update(stock_id=stock.id, bond_id=None)
    else:
        bond = BondService(session).require(payload.bond or "")
        values.update(bond_id=bond.id, stock_id=None)
    row = AlertRepository(session).add(**values)
    session.commit()
    return _serialize(row)


@router.put("/{alert_id}")
def update_alert(alert_id: int, payload: AlertUpdate, session: Session = Depends(get_session), identity: Identity = Depends(require_owner)) -> dict:
    row = AlertRepository(session).get_for_owner(alert_id, user_id=identity.user_id, token=identity.token)
    if row is None:
        raise NotFoundError(f"Alert not found: {alert_id}")
    row.is_active = payload.is_active
    session.commit()
    return _serialize(row)


@router.delete("/{alert_id}", status_code=204)
def delete_alert(alert_id: int, session: Session = Depends(get_session), identity: Identity = Depends(require_owner)) -> None:
    repo = AlertRepository(session)
    row = repo.get_for_owner(alert_id, user_id=identity.user_id, token=identity.token)
    if row is None:
        raise NotFoundError(f"Alert not found: {alert_id}")
    repo.remove(row)
    session.commit()
