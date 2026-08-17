from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.services.news_queries import NewsQueryService

router=APIRouter()

@router.get("/internal/statistics", include_in_schema=False)
def statistics(session:Session=Depends(get_session))->dict: return NewsQueryService(session).statistics()

@router.get("/{event_id}")
def event_detail(event_id:int,session:Session=Depends(get_session))->dict: return NewsQueryService(session).event(event_id)

@router.get("/{event_id}/historical-analogs")
def historical_analogs(event_id:int,session:Session=Depends(get_session))->dict: return NewsQueryService(session).event(event_id)["historical_analogs"]
