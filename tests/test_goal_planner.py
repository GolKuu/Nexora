from __future__ import annotations

from datetime import date

import pytest

from app.services.goal_planner import classify_feasibility, future_value, required_annual_return


def test_required_return_is_ten_percent_for_one_year_without_contributions():
    result = required_annual_return(5_000_000, 5_500_000, 12, 0)
    assert result == pytest.approx(0.10, abs=1e-8)


def test_required_return_accounts_for_end_of_month_contributions():
    result = required_annual_return(1_000_000, 1_240_000, 12, 20_000)
    assert result <= 0.001
    assert future_value(1_000_000, 20_000, 12, result) == pytest.approx(1_240_000, abs=0.01)


def test_unrealistic_goal_does_not_become_feasible_for_balanced_profile():
    required = required_annual_return(500_000, 2_000_000, 12, 0)
    assert classify_feasibility(required, "balanced") == "UNREALISTIC"


def test_goal_plan_returns_executable_quantities_and_visible_downside(api):
    response = api.post("/investment-goals/plan", json={
        "starting_capital": 5_000_000,
        "target_type": "FINAL_VALUE",
        "target_amount": 5_500_000,
        "horizon_months": 12,
        "monthly_contribution": 0,
        "risk_profile": "balanced",
        "currency": "KZT",
    }, headers={"X-Anon-Token": "goal-planner-test"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["required_return"] == pytest.approx(0.10, abs=1e-6)
    assert body["feasibility"] == "FEASIBLE"
    assert body["scenarios"]["negative"]["final_value"] < body["scenarios"]["positive"]["final_value"]
    assert body["initial_portfolio"]
    assert all(row["quantity"] > 0 and row["purchase_cost"] > 0 for row in body["initial_portfolio"])
    assert body["cash_remaining"] >= 0
    assert body["goal_id"] is not None


def test_unrealistic_goal_returns_safer_alternatives(api):
    response = api.post("/investment-goals/plan", json={
        "starting_capital": 500_000, "target_type": "FINAL_VALUE", "target_amount": 2_000_000,
        "horizon_months": 12, "monthly_contribution": 0, "risk_profile": "balanced", "currency": "KZT",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["feasibility"] == "UNREALISTIC"
    assert {row["kind"] for row in body["alternative_plans"]} == {
        "INCREASE_CAPITAL", "ADD_MONTHLY_CONTRIBUTION", "EXTEND_HORIZON"
    }
    assert "слишком агрессивная" in body["warnings"][0]


def test_editing_quantity_recalculates_and_appends_version(api):
    headers = {"X-Anon-Token": "goal-edit-test"}
    plan = api.post("/investment-goals/plan", json={
        "starting_capital": 5_000_000, "target_type": "FINAL_VALUE", "target_amount": 5_500_000,
        "horizon_months": 12, "monthly_contribution": 0, "risk_profile": "balanced", "currency": "KZT",
    }, headers=headers).json()
    positions = [{"ticker": row["ticker"], "quantity": row["quantity"]} for row in plan["initial_portfolio"]]
    positions[0]["quantity"] = max(0, positions[0]["quantity"] - plan["initial_portfolio"][0]["lot_size"])
    edited = api.put(f"/investment-goals/{plan['goal_id']}/plan", json={"positions": positions}, headers=headers)
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["version"] == 2
    assert body["cash_remaining"] >= plan["cash_remaining"]
    assert body["target_progress"]["projected_final_value"] == body["scenarios"]["base"]["final_value"]


def test_copy_is_idempotent_and_execution_preserves_plan(api):
    headers = {"X-Anon-Token": "goal-copy-test"}
    plan = api.post("/investment-goals/plan", json={
        "starting_capital": 5_000_000, "target_type": "PROFIT", "target_amount": 500_000,
        "horizon_months": 12, "monthly_contribution": 0, "risk_profile": "balanced", "currency": "KZT",
    }, headers=headers).json()
    first = api.post(f"/investment-goals/{plan['goal_id']}/copy-to-portfolio", headers=headers)
    assert first.status_code == 200, first.text
    second = api.post(f"/investment-goals/{plan['goal_id']}/copy-to-portfolio", headers=headers).json()
    assert second["already_copied"] is True
    portfolio = api.get(f"/portfolios/{first.json()['portfolio_id']}", headers=headers).json()
    assert portfolio["summary"]["position_count"] == 0

    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.models.portfolio import PortfolioPosition
    with SessionLocal() as session:
        planned = session.scalar(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == first.json()["portfolio_id"]))
        position_id = planned.id
        planned_quantity = planned.planned_quantity
    executed = api.post(f"/investment-goals/{plan['goal_id']}/positions/{position_id}/mark-executed", json={
        "actual_quantity": planned_quantity, "actual_price": 100.0,
        "actual_commission": 125.0, "execution_date": date.today().isoformat(),
    }, headers=headers)
    assert executed.status_code == 200, executed.text
    assert executed.json()["planned_quantity"] == planned_quantity
    assert executed.json()["status"] == "EXECUTED"

    replanned = api.post(f"/investment-goals/{plan['goal_id']}/replan", headers=headers)
    assert replanned.status_code == 200, replanned.text
    assert replanned.json()["version"] == 2
    history = api.get(f"/investment-goals/{plan['goal_id']}", headers=headers).json()
    assert [row["version"] for row in history["versions"]] == [1, 2]
