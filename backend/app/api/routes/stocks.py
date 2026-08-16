from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.collectors.kase_stock_catalog import KaseStockCatalogCollector
from app.models.stock import Stock
from app.schemas.stocks import StockCompareRequest, StockInvestmentRequest, StockRecommendRequest, UniversalSearchRequest
from app.services.stock_service import StockService
from app.services.stock_analyst import StockAnalystService

router = APIRouter()


@router.get("")
def list_stocks(q: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), profile: str = "balanced", session: Session = Depends(get_session)) -> dict:
    return StockService(session).list(query=q, limit=limit, offset=offset, profile=profile)


@router.get("/search")
def search_stocks(q: str = Query(min_length=1), limit: int = Query(20, ge=1, le=100), session: Session = Depends(get_session)) -> dict:
    return StockService(session).list(query=q, limit=limit)


@router.post("/search")
def interpret_stock_search(payload: UniversalSearchRequest, session: Session = Depends(get_session)) -> dict:
    return StockService(session).interpret_search(payload.query, payload.limit)


@router.get("/top")
def top_stocks(category: str = "best", limit: int = Query(10, ge=1, le=100), session: Session = Depends(get_session)) -> dict:
    payload = StockService(session).list(limit=500)
    kind = {"best": "investment", "quality": "quality", "undervalued": "valuation", "growth": "growth", "dividends": "dividend", "liquid": "liquidity", "low_risk": "risk", "momentum": "momentum"}.get(category, "investment")
    reverse = kind != "risk"
    if reverse:
        payload["items"].sort(key=lambda row: (row["scores"][kind]["value"] is not None, row["scores"][kind]["value"] or -1), reverse=True)
    else:
        payload["items"].sort(key=lambda row: (row["scores"][kind]["value"] is None, row["scores"][kind]["value"] if row["scores"][kind]["value"] is not None else 101))
    payload.update(items=payload["items"][:limit], limit=limit, category=category)
    return payload


@router.post("/recommend")
def recommend_stocks(payload: StockRecommendRequest, session: Session = Depends(get_session)) -> dict:
    return StockService(session).recommend(payload)


@router.post("/compare")
def compare_stocks(payload: StockCompareRequest, session: Session = Depends(get_session)) -> dict:
    service = StockService(session); columns = []
    for identifier in payload.identifiers:
        card = service.card(identifier)
        calculation = service.calculate(identifier, StockInvestmentRequest(amount=payload.amount, scenario=payload.scenario)) if payload.amount else None
        columns.append({"ticker": card["ticker"], "company_name": card["company_name"], "price": card["price"], "market_cap": card["market_cap"], "metrics": card["metrics"], "scores": card["scores"], "investment_calculation": calculation})
    return {"columns": columns, "amount": payload.amount, "scenario": payload.scenario, "warning": "Сценарии не являются прогнозом будущей цены."}


@router.post("/refresh")
async def refresh_stocks(session: Session = Depends(get_session)) -> dict:
    result = await KaseStockCatalogCollector(session).collect()
    service = StockService(session)
    for stock in session.execute(select(Stock)).scalars():
        service.persist_metrics_and_scores(stock)
    session.commit()
    return result


@router.post("/{identifier}/investment-calculation")
def investment_calculation(identifier: str, payload: StockInvestmentRequest, session: Session = Depends(get_session)) -> dict:
    return StockService(session).calculate(identifier, payload)


@router.get("/{identifier}")
def get_stock(identifier: str, profile: str = "balanced", session: Session = Depends(get_session)) -> dict:
    return StockService(session).card(identifier, profile)


@router.get("/{identifier}/peers")
def stock_peers(identifier: str, limit: int = Query(8, ge=1, le=20), session: Session = Depends(get_session)) -> dict:
    return StockService(session).peers(identifier, limit)


@router.get("/{identifier}/financial-changes")
def stock_financial_changes(identifier: str, session: Session = Depends(get_session)) -> dict:
    return StockService(session).financial_change_analysis(identifier)


@router.get("/{identifier}/history")
def stock_history(identifier: str, limit: int = Query(252, ge=1, le=2000), session: Session = Depends(get_session)) -> dict:
    return StockService(session).history(identifier, limit)


@router.get("/{identifier}/analysis")
async def stock_analysis(identifier: str, question: str | None = None, session: Session = Depends(get_session)) -> dict:
    card = StockService(session).card(identifier)
    explanation = await StockAnalystService().explain(card, question)
    return {"ticker": card["ticker"], "role": "Stock Analyst", "facts": {"metrics": card["metrics"], "scores": card["scores"], "data_timestamp": card["data_timestamp"], "source": card["source"]},
            **explanation, "rules": ["Только проверенные backend-данные", "Отсутствующие показатели не выдумываются", "Сценарий не является прогнозом"]}
