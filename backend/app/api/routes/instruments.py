from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.collectors.kase_stock_catalog import KaseStockCatalogCollector
from app.core.errors import NotFoundError, UpstreamError
from app.repositories.bonds import BondRepository
from app.schemas.stocks import CrossAssetCompareRequest
from app.services.browser_agent_service import BrowserAgentService, require_browser
from app.services.bond_service import BondService
from app.services.change_service import ChangeService, serialize_change
from app.services.stock_service import StockService

router = APIRouter()


def _resolve_instrument(identifier: str, session: Session) -> tuple[str, object]:
    try:
        return "stock", StockService(session).require(identifier)
    except NotFoundError:
        pass
    try:
        return "bond", BondService(session).require(identifier)
    except NotFoundError:
        raise NotFoundError(f"Инструмент не найден: {identifier}") from None


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


@router.post("/{identifier}/refresh", summary="Обновить один инструмент")
async def refresh_instrument(
    identifier: str,
    force: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> dict:
    """Refresh a bond through Chromium or a share via KASE's public feed."""
    try:
        instrument_type, instrument = _resolve_instrument(identifier, session)
    except NotFoundError:
        # The public stock catalogue is also the discovery mechanism for an
        # unseen share. It is queried once; no ticker URL is guessed.
        try:
            await KaseStockCatalogCollector(session).collect()
        except Exception as exc:
            raise UpstreamError(
                "Не удалось обновить публичный каталог KASE.",
                details={"error": str(exc)},
            ) from exc
        try:
            instrument_type, instrument = _resolve_instrument(identifier, session)
        except NotFoundError:
            # The identifier may be a bond absent from the local catalogue.
            # Search KASE and let the browser agent confirm identity; it never
            # guesses an instrument URL.
            require_browser()
            payload = await BrowserAgentService(session).verify_bond(
                identifier, force=force
            )
            return {"instrument_type": "bond", "discovered": True, **payload}

    if instrument_type == "bond":
        require_browser()
        payload = await BrowserAgentService(session).verify_bond(
            identifier, force=force
        )
        return {"instrument_type": "bond", **payload}

    stock = instrument
    before = len(
        ChangeService(session).for_entity(
            str(stock.id), entity_type="stock", limit=5000
        )
    )
    try:
        stats = await KaseStockCatalogCollector(session).collect()
    except Exception as exc:
        raise UpstreamError(
            "Не удалось обновить акцию из публичного источника KASE.",
            details={"error": str(exc)},
        ) from exc
    stock = StockService(session).require(identifier)
    after = len(
        ChangeService(session).for_entity(
            str(stock.id), entity_type="stock", limit=5000
        )
    )
    return {
        "instrument_type": "stock",
        "ticker": stock.instrument.ticker,
        "status": "changed" if after > before else "unchanged",
        "changed": after > before,
        "changes_created": max(0, after - before),
        "source": "kase_public_website",
        "force": force,
        "collector": stats,
    }


@router.get("/{identifier}/changes", summary="История изменений инструмента")
def instrument_changes(
    identifier: str,
    since: datetime | None = Query(default=None),
    section: str | None = Query(default=None),
    importance: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[dict]:
    instrument_type, instrument = _resolve_instrument(identifier, session)
    rows = ChangeService(session).for_entity(
        str(instrument.id), entity_type=instrument_type, since=since,
        section=section, importance=importance, limit=limit,
    )
    return [serialize_change(row) for row in rows]


@router.get("/{identifier}/change-summary", summary="Сводка изменений инструмента")
def instrument_change_summary(
    identifier: str,
    since: datetime | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    instrument_type, instrument = _resolve_instrument(identifier, session)
    service = ChangeService(session)
    result = service.summary(
        str(instrument.id), entity_type=instrument_type, since=since
    )
    freshness = service.freshness(str(instrument.id), entity_type=instrument_type)
    return {
        "instrument_type": instrument_type,
        "ticker": (
            instrument.instrument.ticker
            if instrument_type == "stock"
            else instrument.ticker
        ),
        **result,
        "freshness": {
            key: value.isoformat() if value is not None else None
            for key, value in freshness.items()
        },
    }
