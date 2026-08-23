from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.collectors.kase_stock_catalog import KaseStockCatalogCollector
from app.models.stock import Stock
from app.schemas.stocks import StockCompareRequest, StockInvestmentRequest, StockRecommendRequest, UniversalSearchRequest
from app.services.change_service import ChangeService, serialize_change
from app.services.series_service import MAX_DAYS as MAX_SERIES_DAYS, PublicSeriesService
from app.services.backfill.status import stock_history_coverage
from app.services.chart_service import ChartService
from app.services.stock_service import StockService
from app.services.stock_analyst import StockAnalystService
from app.services.stock_market import ensure_fresh_stock_market
from app.services.stock_ranking import rank_stocks
from app.services.news_queries import NewsQueryService
from app.services.stock_forecast import StockForecastService

router = APIRouter()

@router.get("/{identifier}/news")
def stock_news(identifier: str, limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_session)) -> dict:
    return NewsQueryService(session).news(identifier, limit)

@router.get("/{identifier}/events")
def stock_events(identifier: str, limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_session)) -> dict:
    return NewsQueryService(session).events(identifier, limit)

@router.get("/{identifier}/event-impact")
def stock_event_impact(identifier: str, limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_session)) -> dict:
    return NewsQueryService(session).events(identifier, limit)

@router.get("/{identifier}/daily-drivers")
def stock_daily_drivers(identifier: str, session: Session = Depends(get_session)) -> dict:
    return NewsQueryService(session).daily_drivers(identifier)


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
async def top_stocks(category: str = "best", limit: int = Query(10, ge=1, le=100), session: Session = Depends(get_session)) -> dict:
    refresh = await ensure_fresh_stock_market(session)
    payload = StockService(session).list(limit=500)
    ranking = rank_stocks(payload["items"], category, limit)
    ranking["market_refresh"] = refresh
    return ranking


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
    service = StockService(session)
    requested_quantity = payload.quantity if payload.mode == "quantity" else None
    if requested_quantity is not None:
        probe = service.calculate(identifier, payload.model_copy(update={"mode": "amount", "amount": 1e13}))
        unit_price = probe.get("unit_price")
        if unit_price is None:
            return {**probe, "input_mode": "quantity", "requested_quantity": requested_quantity}
        principal = requested_quantity * unit_price
        commission = payload.commission.value if payload.commission.type == "fixed" else principal * payload.commission.value / 100.0
        amount = principal + commission
    else:
        amount = float(payload.amount or 0)
    result = service.calculate(identifier, payload.model_copy(update={"mode": "amount", "amount": amount}))
    return {**result, "input_mode": payload.mode, "requested_quantity": requested_quantity}


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


@router.get("/{identifier}/chart")
def stock_chart(
    identifier: str,
    range: str = Query("1m", pattern="^(1d|5d|1m|3m|6m|1y|2y|3y|5y|max)$"),
    resolution: str = Query("auto", pattern="^(auto|10m|1h|1d|1w|1mo)$"),
    include_events: bool = Query(True),
    session: Session = Depends(get_session),
) -> dict:
    """Price history from stored observations only.

    Days KASE never published stay absent, and ``insufficient_history`` says so
    explicitly - the chart is never padded to make the range look complete.
    """
    stock = StockService(session).require(identifier)
    payload = ChartService(session).series(
        stock.instrument, range_key=range, resolution=resolution
    )
    if not include_events:
        payload["events"] = []
    return payload


@router.get("/{identifier}/history-status")
def stock_history_status(identifier: str, session: Session = Depends(get_session)) -> dict:
    """How much of the requested window this instrument actually has."""
    stock = StockService(session).require(identifier)
    return stock_history_coverage(session, stock.instrument)


@router.get("/{identifier}/series", summary="Дневная серия из публичных данных")
def stock_series(
    identifier: str,
    days: int = Query(365, ge=1, le=MAX_SERIES_DAYS),
    include_licensed: bool = Query(False, description="Включить строки из лицензионного архива KASE"),
    session: Session = Depends(get_session),
) -> dict:
    return PublicSeriesService(session).stock(identifier, days=days, include_licensed=include_licensed)


@router.get("/{identifier}/changes", summary="История реальных изменений акции")
def stock_changes(
    identifier: str,
    since: datetime | None = Query(default=None),
    section: str | None = Query(default=None),
    importance: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[dict]:
    stock = StockService(session).require(identifier)
    return [serialize_change(row) for row in ChangeService(session).for_entity(
        str(stock.id), entity_type="stock", since=since, section=section,
        importance=importance, limit=limit,
    )]


@router.get("/{identifier}/change-summary", summary="Сводка изменений акции")
def stock_change_summary(
    identifier: str,
    since: datetime | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    stock = StockService(session).require(identifier)
    service = ChangeService(session)
    freshness = service.freshness(str(stock.id), entity_type="stock")
    return {
        "ticker": stock.instrument.ticker,
        **service.summary(str(stock.id), entity_type="stock", since=since),
        "freshness": {key: value.isoformat() if value else None for key, value in freshness.items()},
    }


@router.get("/{identifier}/forecast")
async def stock_forecast(identifier: str, horizon: str = Query("20d", pattern=r"^(1|5|20|60)d$"), session: Session = Depends(get_session)) -> dict:
    market_refresh = await ensure_fresh_stock_market(session)
    payload = StockForecastService(session).forecast(identifier, int(horizon[:-1]))
    payload["market_refresh"] = market_refresh
    return payload


@router.get("/{identifier}/forecast-performance")
def stock_forecast_performance(identifier: str, session: Session = Depends(get_session)) -> dict:
    return StockForecastService(session).performance(identifier)


@router.get("/{identifier}/analysis")
async def stock_analysis(identifier: str, question: str | None = None, session: Session = Depends(get_session)) -> dict:
    card = StockService(session).card(identifier)
    explanation = await StockAnalystService().explain(card, question)
    return {"ticker": card["ticker"], "role": "Stock Analyst", "facts": {"metrics": card["metrics"], "scores": card["scores"], "data_timestamp": card["data_timestamp"], "source": card["source"]},
            **explanation, "rules": ["Только проверенные backend-данные", "Отсутствующие показатели не выдумываются", "Сценарий не является прогнозом"]}
