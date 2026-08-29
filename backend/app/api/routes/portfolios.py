from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import Identity, get_identity, require_owner
from app.core.errors import NotFoundError, ValidationError
from app.db.session import get_session
from app.schemas.portfolios import PortfolioCreate, PositionCreate, PositionUpdate
from app.services.bond_service import BondService
from app.services.portfolio_service import PortfolioService
from app.services.stock_service import StockService
from app.services.change_service import ChangeService, serialize_change

router = APIRouter()


@router.get("/{portfolio_id}/changes", summary="Изменения по бумагам портфеля")
def portfolio_changes(
    portfolio_id: int,
    since: datetime | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
) -> list[dict]:
    service = PortfolioService(session)
    _owned(service, portfolio_id, identity)
    return [serialize_change(row) for row in ChangeService(session).portfolio(
        portfolio_id, since=since, limit=limit
    )]


def _owned(service: PortfolioService, portfolio_id: int, identity: Identity):
    portfolio = service.require(portfolio_id)
    owns = (
        portfolio.user_id is not None and portfolio.user_id == identity.user_id
    ) or (
        portfolio.anonymous_token is not None
        and portfolio.anonymous_token == identity.token
    )
    if not owns:
        raise NotFoundError(f"Портфель не найден: {portfolio_id}")
    return portfolio


@router.get("", summary="Мои портфели")
def list_portfolios(
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
) -> dict:
    service = PortfolioService(session)
    rows = service.repo.list_for_owner(user_id=identity.user_id, token=identity.token)
    return {
        "items": [
            {
                "id": p.id,
                "name": p.name,
                "base_currency": p.base_currency,
                "position_count": sum(1 for position in p.positions if position.status == "EXECUTED"),
                "planned_position_count": sum(1 for position in p.positions if position.status == "PLANNED"),
            }
            for p in rows
        ],
        "requires_identity": not identity.has_owner,
    }


@router.post("", status_code=201, summary="Создать портфель")
def create_portfolio(
    payload: PortfolioCreate,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    service = PortfolioService(session)
    portfolio = service.repo.create(
        user_id=identity.user_id,
        anonymous_token=None if identity.user_id else identity.token,
        name=payload.name,
        base_currency=payload.base_currency,
        description=payload.description,
    )
    session.commit()
    return {"id": portfolio.id, "name": portfolio.name}


@router.get("/{portfolio_id}", summary="Портфель с оценкой стоимости")
def get_portfolio(
    portfolio_id: int,
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
) -> dict:
    service = PortfolioService(session)
    portfolio = _owned(service, portfolio_id, identity)
    return service.valuation(portfolio)


@router.post("/{portfolio_id}/positions", status_code=201, summary="Добавить позицию")
def add_position(
    portfolio_id: int,
    payload: PositionCreate,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    service = PortfolioService(session)
    portfolio = _owned(service, portfolio_id, identity)
    if payload.instrument_type == "stock" or payload.stock:
        stock = StockService(session).require(payload.stock or payload.bond or "")
        position = service.repo.add_position(
            portfolio.id, bond_id=None, stock_id=stock.id, instrument_type="stock",
            quantity=payload.quantity, purchase_price=payload.purchase_price,
            purchase_date=payload.purchase_date, fees=payload.fees, note=payload.note,
        )
        session.commit()
        return {"id": position.id, "stock_id": stock.id, "ticker": stock.instrument.ticker, "instrument_type": "stock"}
    if not payload.bond:
        raise ValidationError("Укажите bond или stock.")
    bond = BondService(session).require(payload.bond)
    position = service.repo.add_position(
        portfolio.id,
        bond_id=bond.id,
        stock_id=None,
        instrument_type="bond",
        quantity=payload.quantity,
        purchase_clean_price=payload.purchase_clean_price,
        purchase_date=payload.purchase_date,
        purchase_accrued_interest=payload.purchase_accrued_interest,
        fees=payload.fees,
        note=payload.note,
    )
    session.commit()
    return {"id": position.id, "bond_id": bond.id, "ticker": bond.ticker, "instrument_type": "bond"}


@router.put("/{portfolio_id}/positions/{position_id}", summary="Изменить позицию")
def update_position(
    portfolio_id: int,
    position_id: int,
    payload: PositionUpdate,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    service = PortfolioService(session)
    _owned(service, portfolio_id, identity)
    position = service.repo.get_position(position_id)
    if position is None or position.portfolio_id != portfolio_id:
        raise NotFoundError(f"Позиция не найдена: {position_id}")
    changes = payload.changes()
    if not changes:
        raise ValidationError("Нет полей для изменения.")
    for key, value in changes.items():
        setattr(position, key, value)
    session.commit()
    return {"id": position.id, "updated": sorted(changes)}


@router.delete(
    "/{portfolio_id}/positions/{position_id}", status_code=204, summary="Удалить позицию"
)
def delete_position(
    portfolio_id: int,
    position_id: int,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> None:
    service = PortfolioService(session)
    _owned(service, portfolio_id, identity)
    position = service.repo.get_position(position_id)
    if position is None or position.portfolio_id != portfolio_id:
        raise NotFoundError(f"Позиция не найдена: {position_id}")
    service.repo.delete_position(position)
    session.commit()
