from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Identity, get_identity, require_owner
from app.core.errors import NotFoundError
from app.db.session import get_session
from app.repositories.portfolios import WatchlistRepository
from app.schemas.portfolios import WatchlistCreate
from app.services.bond_service import BondService
from app.services.stock_service import StockService
from app.services.dcf_service import DCFService

router = APIRouter()


@router.get("", summary="Избранное")
def list_watchlist(
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
) -> dict:
    repo = WatchlistRepository(session)
    entries = repo.list_for_owner(user_id=identity.user_id, token=identity.token)
    bond_entries = [e for e in entries if e.bond_id is not None and e.bond is not None]
    items = BondService(session).list_view([e.bond for e in bond_entries])
    notes = {e.bond_id: e.note for e in bond_entries}
    for item in items:
        item["note"] = notes.get(item["id"])
        item["instrument_type"] = "bond"
    stocks = StockService(session)
    stock_items = []
    for entry in entries:
        if entry.stock_id is not None and entry.stock is not None:
            item = stocks.item(entry.stock)
            item["note"] = entry.note
            stock_items.append(item)
            items.append(item)
    summaries = DCFService(session).cached_summaries(
        [item["ticker"] for item in stock_items],
        identity,
        {item["ticker"]: item["price"] for item in stock_items},
    )
    for item in stock_items:
        item["dcf_summary"] = summaries.get(item["ticker"], {"status": "not_calculated"})
    return {"items": items, "requires_identity": not identity.has_owner}


@router.post("", status_code=201, summary="Добавить в избранное")
def add_to_watchlist(
    payload: WatchlistCreate,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    repo = WatchlistRepository(session)
    if payload.stock is not None:
        stock = StockService(session).require(payload.stock)
        existing = repo.find(user_id=identity.user_id, token=identity.token, stock_id=stock.id)
        if existing is not None:
            return {"id": existing.id, "ticker": stock.instrument.ticker, "instrument_type": "stock", "already_present": True}
        entry = repo.add(user_id=identity.user_id, anonymous_token=None if identity.user_id else identity.token,
                         bond_id=None, stock_id=stock.id, instrument_type="stock", note=payload.note)
        session.commit()
        return {"id": entry.id, "ticker": stock.instrument.ticker, "instrument_type": "stock", "already_present": False}
    bond = BondService(session).require(payload.bond)
    existing = repo.find(
        user_id=identity.user_id, token=identity.token, bond_id=bond.id
    )
    if existing is not None:
        return {"id": existing.id, "ticker": bond.ticker, "already_present": True}
    entry = repo.add(
        user_id=identity.user_id,
        anonymous_token=None if identity.user_id else identity.token,
        bond_id=bond.id,
        stock_id=None,
        instrument_type="bond",
        note=payload.note,
    )
    session.commit()
    return {"id": entry.id, "ticker": bond.ticker, "already_present": False}


@router.delete("/{identifier}", status_code=204, summary="Убрать из избранного")
def remove_from_watchlist(
    identifier: str,
    instrument_type: str = "bond",
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> None:
    repo = WatchlistRepository(session)
    if instrument_type == "stock":
        stock = StockService(session).require(identifier)
        entry = repo.find(user_id=identity.user_id, token=identity.token, stock_id=stock.id)
    else:
        bond = BondService(session).require(identifier)
        entry = repo.find(user_id=identity.user_id, token=identity.token, bond_id=bond.id)
    if entry is None:
        raise NotFoundError(f"В избранном нет выпуска {identifier}")
    repo.remove(entry)
    session.commit()
