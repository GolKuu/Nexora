from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Identity, get_identity, require_owner
from app.core.errors import NotFoundError
from app.db.session import get_session
from app.repositories.portfolios import WatchlistRepository
from app.schemas.portfolios import WatchlistCreate
from app.services.bond_service import BondService

router = APIRouter()


@router.get("", summary="Избранное")
def list_watchlist(
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
) -> dict:
    repo = WatchlistRepository(session)
    entries = repo.list_for_owner(user_id=identity.user_id, token=identity.token)
    service = BondService(session)
    items = service.list_view([e.bond for e in entries])
    notes = {e.bond_id: e.note for e in entries}
    for item in items:
        item["note"] = notes.get(item["id"])
    return {"items": items, "requires_identity": not identity.has_owner}


@router.post("", status_code=201, summary="Добавить в избранное")
def add_to_watchlist(
    payload: WatchlistCreate,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    bond = BondService(session).require(payload.bond)
    repo = WatchlistRepository(session)
    existing = repo.find(
        user_id=identity.user_id, token=identity.token, bond_id=bond.id
    )
    if existing is not None:
        return {"id": existing.id, "ticker": bond.ticker, "already_present": True}
    entry = repo.add(
        user_id=identity.user_id,
        anonymous_token=None if identity.user_id else identity.token,
        bond_id=bond.id,
        note=payload.note,
    )
    session.commit()
    return {"id": entry.id, "ticker": bond.ticker, "already_present": False}


@router.delete("/{identifier}", status_code=204, summary="Убрать из избранного")
def remove_from_watchlist(
    identifier: str,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> None:
    bond = BondService(session).require(identifier)
    repo = WatchlistRepository(session)
    entry = repo.find(user_id=identity.user_id, token=identity.token, bond_id=bond.id)
    if entry is None:
        raise NotFoundError(f"В избранном нет выпуска {identifier}")
    repo.remove(entry)
    session.commit()
