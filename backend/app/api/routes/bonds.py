from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.ai.explainer import ExplainerService
from app.api.deps import Identity, get_identity
from app.core.config import settings
from app.core.enums import DataMode
from app.db.session import get_session
from app.schemas.bonds import (
    BondCardResponse,
    BondListResponse,
    CalculatorRequest,
    CashFlowItem,
    HistoryPoint,
)
from app.services.bond_service import BondService
from app.services.calculator_service import CalculatorService
from app.services.peer_service import PeerService
from app.services.scoring_service import ScoringService
from app.services.settings_service import SettingsService

router = APIRouter()

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


@router.get("/top", response_model=BondListResponse, summary="TOP облигаций по оценке")
def top_bonds(
    session: Session = Depends(get_session),
    limit: int = Query(default=10, ge=1, le=50),
    category: str | None = Query(default=None, description="Тип выпуска"),
) -> BondListResponse:
    service = BondService(session)
    items = service.top(limit=limit, category=category)
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
