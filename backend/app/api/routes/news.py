from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.services.news_queries import NewsQueryService

router = APIRouter()


@router.get("", summary="Последние важные новости рынка")
def news_feed(
    limit: int = Query(default=50, ge=1, le=200),
    event_type: str | None = None,
    source: str | None = None,
    min_importance: float = Query(default=0.0, ge=0.0, le=1.0),
    session: Session = Depends(get_session),
) -> dict:
    return NewsQueryService(session).feed(
        limit=limit, event_type=event_type, source=source,
        min_importance=min_importance,
    )
