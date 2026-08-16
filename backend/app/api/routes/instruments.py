from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.repositories.bonds import BondRepository
from app.schemas.stocks import CrossAssetCompareRequest
from app.services.bond_service import BondService
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


@router.post("/compare")
def cross_asset_compare(payload: CrossAssetCompareRequest, session: Session = Depends(get_session)) -> dict:
    """Compare common characteristics while keeping asset-specific formulas separate."""
    bonds = BondService(session)
    stocks = StockService(session)
    items = []
    for request in payload.instruments:
        if request.instrument_type == "stock":
            card = stocks.card(request.identifier)
            items.append({
                "instrument_type": "stock", "ticker": card["ticker"], "name": card["company_name"],
                "risk": card["scores"]["risk"], "liquidity": card["scores"]["liquidity"],
                "potential_income": {"dividend_yield_trailing": card["metrics"].get("trailing_dividend_yield"), "price_change": "scenario_only"},
                "payment_income": "dividends_not_guaranteed", "horizon": "investor_selected",
                "volatility": card["metrics"].get("volatility"), "cashflow_predictability": "low",
                "asset_specific": {"quality": card["scores"]["quality"], "valuation": card["scores"]["valuation"], "growth": card["scores"]["growth"]},
            })
        else:
            bond = bonds.require(request.identifier)
            metric = bonds.metrics.latest(bond.id)
            scores = bonds.scores.latest_all(bond.id)
            items.append({
                "instrument_type": "bond", "ticker": bond.ticker, "name": bond.name,
                "risk": {"value": scores.get("credit").value if scores.get("credit") else None},
                "liquidity": {"value": scores.get("liquidity").value if scores.get("liquidity") else None},
                "potential_income": {"ytm": metric.ytm if metric else None},
                "payment_income": "contractual_coupons_and_principal", "horizon": bond.maturity_date.isoformat() if bond.maturity_date else None,
                "volatility": metric.price_volatility_90d if metric else None, "cashflow_predictability": "higher_if_no_default",
                "asset_specific": {"credit": {"value": scores.get("credit").value if scores.get("credit") else None},
                                   "duration": metric.modified_duration if metric else None},
            })
    return {"items": items, "comparison_type": "cross_asset",
            "explanation": "Акция представляет долю в бизнесе и не имеет договорной доходности; облигация имеет купоны и погашение, но несёт кредитный риск.",
            "warning": "Сценарный рост акции не сопоставляется с YTM облигации как гарантированный доход."}
