from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import Identity, get_identity, require_owner
from app.core.errors import NotFoundError, ValidationError
from app.db.session import get_session
from app.models.portfolio import GoalPlanVersion, PortfolioPosition
from app.schemas.investment_goals import ExecutePositionRequest, GoalPlanRequest, PlanEditRequest
from app.services.goal_planner import GoalPlannerService

router = APIRouter()


@router.post("/plan", summary="Построить детерминированный план достижения цели")
def create_plan(
    payload: GoalPlanRequest,
    session: Session = Depends(get_session),
    identity: Identity = Depends(get_identity),
) -> dict:
    result = GoalPlannerService(session).plan(
        payload, user_id=identity.user_id, token=identity.token, persist=identity.has_owner
    )
    session.commit()
    return result


@router.get("/{goal_id}", summary="Текущая версия инвестиционной цели")
def get_goal(
    goal_id: int,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    service = GoalPlannerService(session)
    goal = service.owned_goal(goal_id, user_id=identity.user_id, token=identity.token)
    versions = list(session.scalars(select(GoalPlanVersion).where(GoalPlanVersion.goal_id == goal.id).order_by(GoalPlanVersion.version)))
    current = next((row for row in versions if row.version == goal.current_version), None)
    if current is None:
        raise NotFoundError("Текущая версия плана не найдена.")
    return {**current.plan_snapshot, "goal_id": goal.id, "version": current.version,
            "versions": [{"version": row.version, "created_at": row.created_at.isoformat(),
                          "methodology_version": row.methodology_version} for row in versions]}


@router.post("/{goal_id}/copy-to-portfolio", summary="Скопировать план в портфель как PLANNED")
def copy_to_portfolio(
    goal_id: int,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    service = GoalPlannerService(session)
    goal = service.owned_goal(goal_id, user_id=identity.user_id, token=identity.token)
    result = service.copy_to_portfolio(goal, user_id=identity.user_id, token=identity.token)
    session.commit()
    return result


@router.post("/{goal_id}/replan", summary="Обновить план без перезаписи истории")
def replan_goal(
    goal_id: int,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    service = GoalPlannerService(session)
    goal = service.owned_goal(goal_id, user_id=identity.user_id, token=identity.token)
    result = service.replan(goal, user_id=identity.user_id, token=identity.token)
    session.commit()
    return result


@router.put("/{goal_id}/plan", summary="Изменить количества и создать новую версию плана")
def edit_goal_plan(
    goal_id: int,
    payload: PlanEditRequest,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    service = GoalPlannerService(session)
    goal = service.owned_goal(goal_id, user_id=identity.user_id, token=identity.token)
    result = service.edit_plan(goal, payload)
    session.commit()
    return result


@router.post("/{goal_id}/positions/{position_id}/mark-executed", summary="Подтвердить фактическую покупку")
def mark_executed(
    goal_id: int,
    position_id: int,
    payload: ExecutePositionRequest,
    session: Session = Depends(get_session),
    identity: Identity = Depends(require_owner),
) -> dict:
    service = GoalPlannerService(session)
    service.owned_goal(goal_id, user_id=identity.user_id, token=identity.token)
    position = session.get(PortfolioPosition, position_id)
    if position is None or position.goal_id != goal_id or position.status != "PLANNED":
        raise NotFoundError(f"Плановая позиция не найдена: {position_id}")
    if payload.actual_quantity > (position.planned_quantity or position.quantity) + 1e-9:
        raise ValidationError("Фактическое количество выше планового; сначала обновите план.")
    position.status = "EXECUTED"
    position.actual_quantity = payload.actual_quantity
    position.actual_price = payload.actual_price
    position.actual_commission = payload.actual_commission
    position.execution_date = payload.execution_date
    position.quantity = payload.actual_quantity
    position.purchase_date = payload.execution_date
    position.fees = payload.actual_commission
    if position.instrument_type == "stock":
        position.purchase_price = payload.actual_price
    else:
        position.purchase_clean_price = payload.actual_price
    session.commit()
    return {"id": position.id, "status": position.status, "planned_quantity": position.planned_quantity,
            "actual_quantity": position.actual_quantity, "actual_price": position.actual_price,
            "actual_commission": position.actual_commission, "execution_date": position.execution_date.isoformat()}
