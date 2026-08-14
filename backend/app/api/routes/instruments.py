from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.repositories.bonds import BondRepository
from app.services.stock_service import StockService

router = APIRouter()


@router.get("/search")
def universal_search(q: str = Query(min_length=1), limit: int = Query(20, ge=1, le=100), session: Session = Depends(get_session)) -> dict:
    stocks = StockService(session).list(query=q, limit=limit)["items"]
    bonds = BondRepository(session).search(q, limit)
    items = [{"id": row["id"], "ticker": row["ticker"], "isin": row["isin"], "name": row["company_name"], "instrument_type": "stock", "type_label": row["type_label"], "href": f"/stock/{row['ticker']}"} for row in stocks]
    items.extend({"id": bond.id, "ticker": bond.ticker, "isin": bond.isin, "name": bond.name, "instrument_type": "bond", "type_label": "Облигация", "href": f"/bond/{bond.ticker}"} for bond in bonds)
    exact = q.strip().upper()
    items.sort(key=lambda row: (0 if row["ticker"].upper() == exact or (row["isin"] or "").upper() == exact else 1, row["ticker"]))
    return {"items": items[:limit], "total": len(items), "query": q}
