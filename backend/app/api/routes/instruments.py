import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.history import MarketObservation
from app.models.market import BondQuote
from app.collectors.kase_stock_catalog import KaseStockCatalogCollector
from app.core.errors import NotFoundError, StreamingUnsupportedError, UpstreamError
from app.repositories.bonds import BondRepository
from app.schemas.stocks import CrossAssetCompareRequest
from app.services.browser_agent_service import BrowserAgentService, require_browser
from app.services.bond_service import BondService
from app.services.change_service import ChangeService, serialize_change
from app.services.chart_service import ChartService, RANGES, resolve_range
from app.services.score_history import ScoreHistoryService
from app.services.series_service import MAX_DAYS as MAX_SERIES_DAYS, PublicSeriesService
from app.services.stock_service import StockService

router = APIRouter()

#: How often the stream re-reads stored state. Well under the ten-minute
#: collection cadence, so a new observation surfaces promptly, and far too slow
#: to be mistaken for a trading feed.
_STREAM_POLL_SECONDS = 15


def _sse(event: str, payload: dict) -> str:
    """One server-sent event frame."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


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


@router.get(
    "/{identifier}/stream",
    summary="Поток обновлений инструмента (SSE)",
    response_class=StreamingResponse,
)
async def instrument_stream(
    identifier: str,
    request: Request,
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """Server-sent events: one message when this instrument's stored data moves.

    The page keeps its chart current without a reload and without ever talking
    to KASE itself - the collector writes to the database, and this endpoint
    reports what the database now holds. Nothing is pushed that was not first
    validated and stored, so a reader can never see a number the API would
    disagree with.

    This is a ten-minute public-web cadence, not a trading feed: the event says
    *something changed*, and the client re-reads the same endpoints it would
    have read anyway.
    """
    if settings.is_serverless:
        # A long-lived generator inside a serverless request handler would be
        # billed for its whole life and killed mid-stream anyway. The client
        # falls back to polling when it sees this.
        raise StreamingUnsupportedError(
            "Поток недоступен в serverless-развёртывании; используйте опрос."
        )

    kind, entity = _resolve_instrument(identifier, session)
    # A stock carries its identity on the linked Instrument row; a bond
    # carries its own. Resolve both once rather than at every yield.
    instrument_id = entity.instrument.id if kind == "stock" else entity.id
    ticker = entity.instrument.ticker if kind == "stock" else entity.ticker

    def latest(db: Session) -> str | None:
        """The newest stored moment for this instrument, or None."""
        if kind == "stock":
            value = db.scalar(
                select(func.max(MarketObservation.observed_at)).where(
                    MarketObservation.instrument_id == instrument_id,
                    MarketObservation.superseded_at.is_(None),
                )
            )
        else:
            value = db.scalar(
                select(func.max(BondQuote.timestamp)).where(
                    BondQuote.bond_id == instrument_id
                )
            )
        return value.isoformat() if value else None

    async def events() -> AsyncIterator[str]:
        # A short-lived session per poll: holding one open for the life of the
        # stream would pin a connection from the pool for hours.
        probe = SessionLocal()
        try:
            last = latest(probe)
        finally:
            probe.close()

        yield _sse(
            "connected",
            {
                "instrument": ticker,
                "kind": kind,
                "last_updated": last,
                "poll_seconds": _STREAM_POLL_SECONDS,
                "data_mode": settings.KASE_DATA_MODE,
            },
        )

        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(_STREAM_POLL_SECONDS)
            probe = SessionLocal()
            try:
                current = latest(probe)
            except Exception:  # a transient database blip must not kill the page
                probe.rollback()
                current = last
            finally:
                probe.close()

            if current != last:
                last = current
                yield _sse(
                    "update",
                    {"instrument": ticker, "kind": kind, "last_updated": current},
                )
            else:
                # A comment frame keeps proxies from closing an idle stream and
                # is ignored by EventSource. Unchanged data is not an event:
                # duplicate observations are never invented to fill the silence.
                yield ": keep-alive\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{identifier}/chart", summary="Историческая серия инструмента")
def instrument_chart(
    identifier: str,
    range: str = Query("1m", pattern="^(1d|5d|1m|3m|6m|1y|2y|3y|5y|max)$"),
    resolution: str = Query("auto", pattern="^(auto|10m|1h|1d|1w|1mo)$"),
    include_events: bool = Query(True),
    include_scores: bool = Query(False),
    session: Session = Depends(get_session),
) -> dict:
    """One chart endpoint for both asset classes, keyed by ticker, ISIN or id.

    Stocks answer from the canonical observation history; bonds answer from the
    stored public snapshots, whose bars carry the extra ``ytm`` the equity path
    has no use for. Both come back in the same envelope so one frontend chart
    can render either.

    Only stored facts are returned. A range longer than the stored history
    reports the shortfall in ``insufficient_history`` rather than padding the
    line to make the window look complete.
    """
    kind, entity = _resolve_instrument(identifier, session)

    if kind == "stock":
        payload = ChartService(session).series(
            entity.instrument, range_key=range, resolution=resolution
        )
    else:
        days = RANGES.get(resolve_range(range))
        series = PublicSeriesService(session).bond(
            identifier, days=min(days or MAX_SERIES_DAYS, MAX_SERIES_DAYS)
        )
        coverage = series.get("coverage") or {}
        sessions = series.get("sessions") or []
        payload = {
            "instrument": {
                "id": entity.id,
                "ticker": entity.ticker,
                "isin": entity.isin,
                "currency": entity.currency,
                "type": "bond",
                "is_active": entity.is_active,
                "kase_url": entity.kase_url,
            },
            "range": resolve_range(range),
            # Bond bars are folded per day; a finer resolution would imply
            # intraday depth these snapshots do not carry.
            "resolution": "1d",
            "requested_start": None,
            "requested_end": datetime.now(timezone.utc).isoformat(),
            "series": sessions,
            "points": len(sessions),
            "traded_points": sum(1 for row in sessions if row.get("close") is not None),
            "events": series.get("markers") or [],
            "price_unit": series.get("price_unit"),
            "source": coverage.get("sources") or series.get("basis"),
            "data_mode": coverage.get("data_mode"),
            "last_updated": sessions[-1]["timestamp"] if sessions else None,
            "coverage": coverage,
            "insufficient_history": series.get("warning"),
        }

    if not include_events:
        payload["events"] = []
    if include_scores:
        payload["scores"] = ScoreHistoryService(session).history(
            identifier, limit=200
        ).get("snapshots", [])
    # The brief names this field `historical_coverage`; the older per-asset
    # chart routes already ship `coverage`. Both point at the same object.
    payload["historical_coverage"] = payload.get("coverage")
    payload["instrument_kind"] = kind
    return payload


@router.get("/{identifier}/score-history", summary="История оценки инструмента")
def score_history(
    identifier: str,
    kind: str | None = Query(default=None, pattern="^(bond|stock|bank)$"),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    """Every score ever published for this instrument, newest first.

    Accepts a ticker, an ISIN or a numeric id, for stocks and bonds alike -
    whatever the user typed into the search box works here too.

    Alongside the snapshots comes one entry per transition explaining what moved
    the score: which component changed, which red flag was raised, which hard cap
    started binding, and whether the model version changed rather than the facts.
    Nothing is recomputed; the explanation is a comparison of the two stored
    breakdowns, so it can never disagree with the numbers it explains.
    """
    return ScoreHistoryService(session).history(identifier, kind=kind, limit=limit)


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
