from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.ai.explainer import ExplainerService
from app.api.deps import Identity, get_identity
from app.core.config import settings
from app.core.enums import DataMode
from app.db.session import get_session
from app.providers.inflation import get_inflation
from app.schemas.bonds import (
    BondCardResponse,
    BondListResponse,
    CalculatorRequest,
    CashFlowItem,
    HistoryPoint,
    InvestmentCalculationRequest,
    InvestmentCalculationResponse,
    RecommendRequest,
    RecommendResponse,
)
from app.services.bond_service import BondService
from app.services.calculator_service import CalculatorService
from app.services.investment_service import InvestmentService
from app.services.peer_service import PeerService
from app.services.recommendation_service import RecommendationService
from app.services.scoring_service import ScoringService
from app.services.series_service import MAX_DAYS as MAX_SERIES_DAYS, PublicSeriesService
from app.services.settings_service import SettingsService
from app.services.change_service import ChangeService, serialize_change

router = APIRouter()


@router.get("/{identifier}/changes", summary="История реальных изменений выпуска")
def bond_changes(
    identifier: str,
    since: datetime | None = Query(default=None),
    section: str | None = Query(default=None),
    importance: int | None = Query(default=None, ge=0, le=100),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> list[dict]:
    bond = BondService(session).require(identifier)
    return [serialize_change(row) for row in ChangeService(session).for_entity(
        str(bond.id), entity_type="bond", since=since, section=section,
        importance=importance, limit=limit
    )]


@router.get("/{identifier}/change-summary", summary="Сводка изменений выпуска")
def bond_change_summary(
    identifier: str,
    since: datetime | None = Query(default=None),
    session: Session = Depends(get_session),
) -> dict:
    bond = BondService(session).require(identifier)
    return ChangeService(session).summary(
        str(bond.id), entity_type="bond", since=since
    )

MOCK_WARNING = (
    "Показаны демонстрационные данные. KASE не подключен, цифры синтетические."
)


def _warning(data_modes: list[str | None]) -> str | None:
    return MOCK_WARNING if DataMode.MOCK.value in data_modes else None


def _profile(session: Session, identity: Identity) -> str:
    prefs = SettingsService(session).get(user_id=identity.user_id, token=identity.token)
    return prefs.get("risk_profile", "balanced")


@router.get("", response_model=BondListResponse, summary="Список облигаций")
def list_bonds(
    session: Session = Depends(get_session),
    bond_type: str | None = None,
    currency: str | None = None,
    max_years: float | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> BondListResponse:
    service = BondService(session)
    bonds = service.bonds.list(
        bond_type=bond_type,
        currency=currency,
        max_years=max_years,
        limit=limit,
        offset=offset,
    )
    items = service.list_view(bonds)
    modes = [i["data_mode"] for i in items]
    return BondListResponse(
        items=items,
        total=service.bonds.count(),
        limit=limit,
        offset=offset,
        data_mode=modes[0] if modes else None,
        warning=_warning(modes),
    )


#: Sort keys accepted by /bonds/top (§36). Anything else is rejected rather
#: than silently ignored, so the client never believes it sorted by a field it
#: misspelled.
TOP_SORTS = {
    "investment_score",
    "credit_score",
    "liquidity_score",
    "growth_score",
    "hold_score",
    "trade_score",
    "real_return",
}


@router.get("/top", response_model=BondListResponse, summary="TOP облигаций по оценке")
def top_bonds(
    session: Session = Depends(get_session),
    # A hundred, so a TOP 100 is one request rather than two pages stitched
    # together - the same ceiling /stocks/top already allows.
    limit: int = Query(default=10, ge=1, le=100),
    category: str | None = Query(default=None, description="Тип выпуска"),
    exclude_government: bool = Query(
        default=False,
        description="Исключить государственные ценные бумаги",
    ),
    currency: str | None = Query(default=None),
    min_maturity_years: float | None = Query(default=None, ge=0),
    max_maturity_years: float | None = Query(default=None, gt=0),
    min_ytm: float | None = Query(default=None, description="Доходность в %, минимум"),
    min_real_ytm: float | None = Query(default=None, description="Реальная доходность в %"),
    min_credit_score: float | None = Query(default=None, ge=0, le=100),
    sort: str = Query(default="investment_score"),
) -> BondListResponse:
    if sort not in TOP_SORTS:
        raise HTTPException(
            status_code=422,
            detail=f"sort must be one of: {', '.join(sorted(TOP_SORTS))}",
        )
    service = BondService(session)
    items = service.top(
        limit=limit,
        category=category,
        exclude_government=exclude_government,
        currency=currency,
        min_maturity_years=min_maturity_years,
        max_maturity_years=max_maturity_years,
        min_ytm=min_ytm,
        min_real_ytm=min_real_ytm,
        min_credit_score=min_credit_score,
        sort=sort,
    )
    modes = [i["data_mode"] for i in items]
    return BondListResponse(
        items=items,
        total=len(items),
        limit=limit,
        offset=0,
        data_mode=modes[0] if modes else None,
        warning=_warning(modes),
    )


@router.get("/search", response_model=BondListResponse, summary="Поиск по тикеру, ISIN, названию")
def search_bonds(
    q: str = Query(min_length=1, max_length=64),
    session: Session = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=50),
) -> BondListResponse:
    """Exact ticker and ISIN matches rank above every substring hit (§37)."""
    service = BondService(session)
    items = service.list_view(service.bonds.search(q, limit=limit))
    modes = [i["data_mode"] for i in items]
    return BondListResponse(
        items=items,
        total=len(items),
        limit=limit,
        offset=0,
        data_mode=modes[0] if modes else None,
        warning=_warning(modes),
    )


@router.post(
    "/recommend",
    response_model=RecommendResponse,
    summary="Подбор облигаций под сумму и профиль",
    description=(
        "Отбор и ранжирование выполняет backend по сохраненным метрикам и "
        "оценкам. Языковая модель в ранжировании не участвует — она может "
        "только объяснить готовый список по полю reason_codes."
    ),
)
def recommend_bonds(
    payload: RecommendRequest,
    session: Session = Depends(get_session),
) -> RecommendResponse:
    result = RecommendationService(session).recommend(
        amount=payload.amount,
        currency=payload.currency,
        max_maturity_years=payload.max_maturity_years,
        min_maturity_years=payload.min_maturity_years,
        profile=payload.profile,
        inflation_enabled=payload.inflation_enabled,
        limit=payload.limit,
        commission_type=payload.commission.type,
        commission_value=payload.commission.value,
    )
    return RecommendResponse(**result)


@router.get("/{identifier}", response_model=BondCardResponse, summary="Карточка облигации")
def get_bond(identifier: str, session: Session = Depends(get_session)) -> BondCardResponse:
    service = BondService(session)
    card = service.card(service.require(identifier))
    card["warning"] = MOCK_WARNING if card["freshness"]["is_mock"] else None
    return BondCardResponse(**card)


@router.get("/{identifier}/metrics", summary="Технические показатели")
def get_metrics(identifier: str, session: Session = Depends(get_session)) -> dict:
    service = BondService(session)
    bond = service.require(identifier)
    metric = service.metrics.latest(bond.id)
    quote = service.quotes.latest(bond.id)
    return {
        "ticker": bond.ticker,
        "metrics": service.pro_view(bond, metric, quote),
        "freshness": service.freshness(
            quote.timestamp if quote else None,
            (metric.data_mode if metric else None) or (quote.data_mode if quote else None),
        ),
    }


@router.get("/{identifier}/scores", summary="Все оценки выпуска")
def get_scores(
    identifier: str,
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
    recompute: bool = Query(default=False, description="Пересчитать перед выдачей"),
) -> dict:
    service = BondService(session)
    bond = service.require(identifier)
    profile = _profile(session, identity)
    if recompute:
        ScoringService(session).compute(bond, risk_profile=profile)
        session.commit()
    rows = service.scores.latest_all(bond.id)
    return {
        "ticker": bond.ticker,
        "risk_profile": profile,
        "model_version": settings.SCORING_MODEL_VERSION,
        "scores": {
            kind: {
                "value": row.value,
                "confidence": row.confidence,
                "version": row.version,
                "calculated_at": row.calculated_at.isoformat(),
                "notes": row.notes,
                "components": [
                    {
                        "code": c.code,
                        "label": c.label,
                        "value": c.value,
                        "weight": c.weight,
                        "contribution": c.contribution,
                        "raw_value": c.raw_value,
                        "raw_unit": c.raw_unit,
                        "available": c.available,
                        "explanation": c.explanation,
                    }
                    for c in row.components
                ],
            }
            for kind, row in rows.items()
        },
    }


@router.get(
    "/{identifier}/score-explanation",
    summary="Почему такая оценка",
    description=(
        "Объяснение строится детерминированно из компонентов оценки. "
        "LLM может только переформулировать текст и никогда не считает числа."
    ),
)
async def score_explanation(
    identifier: str,
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
    kind: str = Query(default="investment"),
    ui_mode: str = Query(default="simple", pattern="^(simple|pro)$"),
    use_ai: bool = Query(default=True),
) -> dict:
    service = BondService(session)
    bond = service.require(identifier)
    profile = _profile(session, identity)
    explanation = ScoringService(session).explanation(bond, kind, risk_profile=profile)
    if not use_ai:
        from app.ai.explainer import deterministic_text

        return {
            "ticker": bond.ticker,
            "text": deterministic_text(explanation),
            "generated_by": "engine",
            "explanation": explanation,
        }
    result = await ExplainerService(session).explain(
        bond.id, explanation, ui_mode=ui_mode
    )
    result["ticker"] = bond.ticker
    return result


@router.get(
    "/{identifier}/cashflows",
    response_model=list[CashFlowItem],
    summary="График выплат",
)
def get_cashflows(identifier: str, session: Session = Depends(get_session)) -> list[CashFlowItem]:
    service = BondService(session)
    bond = service.require(identifier)
    rows = service.cashflow_view(bond)
    if not rows:
        # Nothing stored yet - build it from the bond's own terms.
        from app.services.metrics_service import MetricsService

        MetricsService(session).rebuild_cashflows(bond)
        session.commit()
        rows = service.cashflow_view(bond)
    return [CashFlowItem(**r) for r in rows]


@router.get(
    "/{identifier}/history",
    response_model=list[HistoryPoint],
    summary="История цены и доходности",
)
def get_history(
    identifier: str,
    session: Session = Depends(get_session),
    days: int = Query(default=180, ge=1, le=1825),
) -> list[HistoryPoint]:
    service = BondService(session)
    bond = service.require(identifier)
    return [HistoryPoint(**p) for p in service.history(bond, days=days)]


@router.get("/{identifier}/series", summary="Дневная серия из публичных данных")
def get_series(
    identifier: str,
    days: int = Query(default=365, ge=1, le=MAX_SERIES_DAYS),
    include_licensed: bool = Query(
        default=False, description="Включить строки из лицензионного архива KASE"
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Daily bars folded out of our own public snapshots, licence-free."""
    return PublicSeriesService(session).bond(
        identifier, days=days, include_licensed=include_licensed
    )


@router.get("/{identifier}/peers", summary="Похожие выпуски")
def get_peers(identifier: str, session: Session = Depends(get_session)) -> dict:
    service = BondService(session)
    bond = service.require(identifier)
    peers = PeerService(session)
    return {
        "ticker": bond.ticker,
        "peer_group": bond.peer_group.code if bond.peer_group else None,
        "stats": peers.stats(bond),
        "peers": peers.peers_with_metrics(bond),
    }


@router.post("/{identifier}/calculate", summary="Если вложить X ₸")
def calculate(
    identifier: str,
    payload: CalculatorRequest,
    session: Session = Depends(get_session),
) -> dict:
    service = BondService(session)
    bond = service.require(identifier)
    result = CalculatorService(session).project(
        bond, payload.amount, reinvest_coupons=payload.reinvest_coupons
    )
    result["ticker"] = bond.ticker
    return result


@router.post(
    "/{identifier}/investment-calculation",
    response_model=InvestmentCalculationResponse,
    summary="Расчет инвестиции на произвольную сумму",
    description=(
        "Полный расчет покупки на любую сумму: количество бумаг, НКД, "
        "комиссия, график выплат, прибыль и реальная доходность с поправкой "
        "на инфляцию. Возвращенный номинал прибылью не считается."
    ),
)
def investment_calculation(
    identifier: str,
    payload: InvestmentCalculationRequest,
    session: Session = Depends(get_session),
) -> InvestmentCalculationResponse:
    bond = BondService(session).require(identifier)

    exit_date = date.fromisoformat(payload.exit_date) if payload.exit_date else None
    if payload.exit_mode == "date" and exit_date is None:
        raise HTTPException(
            status_code=422,
            detail="exit_mode='date' requires exit_date (YYYY-MM-DD).",
        )

    horizon = None
    if bond.maturity_date is not None:
        horizon = (bond.maturity_date - date.today()).days / 365.25
    inflation = (
        get_inflation(session, horizon_years=horizon)
        if payload.inflation_enabled
        else None
    )

    result = InvestmentService(session).calculate(
        bond,
        amount=payload.amount,
        commission_type=payload.commission.type,
        commission_value=payload.commission.value,
        inflation_enabled=payload.inflation_enabled,
        inflation=inflation,
        exit_mode=payload.exit_mode,
        exit_date=exit_date,
        scenario=payload.scenario,
    )
    return InvestmentCalculationResponse(**result)
