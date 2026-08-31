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


def test_plan_deploys_the_capital_instead_of_leaving_it_as_cash(api):
    """The allocator used to stop at each name's target weight.

    Every KZT government bond shares one issuer, so the 30% issuer cap bound
    immediately and ~70% of a balanced 5 000 000 ₸ plan silently became cash -
    which then made the plan miss a target its own capital could have reached.
    """
    body = api.post("/investment-goals/plan", json={
        "starting_capital": 5_000_000, "target_type": "FINAL_VALUE", "target_amount": 5_500_000,
        "horizon_months": 12, "monthly_contribution": 0, "risk_profile": "balanced", "currency": "KZT",
    }, headers={"X-Anon-Token": "goal-allocation-test"}).json()

    invested = sum(row["purchase_cost"] for row in body["initial_portfolio"])
    assert invested + body["cash_remaining"] <= 5_000_000 + 1, "spent more than the capital"
    assert body["cash_remaining"] < 5_000_000 * 0.20, (
        f"left {body['cash_remaining']:.0f} ₸ of 5 000 000 ₸ undeployed"
    )

    # Caps must still hold after the spill pass.
    capital = 5_000_000
    per_issuer: dict[int, float] = {}
    for row in body["initial_portfolio"]:
        per_issuer[row["issuer_id"]] = per_issuer.get(row["issuer_id"], 0.0) + row["purchase_cost"]
        if row["instrument_type"] == "stock":
            assert row["purchase_cost"] <= capital * body["constraints"]["max_single_stock_percent"] / 100 + 1
    for spent in per_issuer.values():
        assert spent <= capital * body["constraints"]["max_single_issuer_percent"] / 100 + 1

    # Spreading across issuers is the point: one capped name is not a plan.
    assert len(per_issuer) >= 2


def test_plan_reports_achievable_return_separately_from_feasibility(api):
    """`feasibility` judges the required return; it must not imply the market
    can supply it. The plan reports both numbers and says which one binds."""
    body = api.post("/investment-goals/plan", json={
        "starting_capital": 5_000_000, "target_type": "FINAL_VALUE", "target_amount": 5_500_000,
        "horizon_months": 12, "monthly_contribution": 0, "risk_profile": "balanced", "currency": "KZT",
    }, headers={"X-Anon-Token": "goal-achievable-test"}).json()

    assert "achievable_return_pct" in body
    assert body["plan_reaches_target"] == body["scenarios"]["base"]["target_reached"]
    assert body["return_gap_pct"] == pytest.approx(
        body["required_return_pct"] - body["achievable_return_pct"], abs=0.02
    )
    if not body["plan_reaches_target"]:
        assert any("не достигает цели" in w for w in body["warnings"])


def test_unrealistic_goal_returns_safer_alternatives(api):
    response = api.post("/investment-goals/plan", json={
        "starting_capital": 500_000, "target_type": "FINAL_VALUE", "target_amount": 2_000_000,
        "horizon_months": 12, "monthly_contribution": 0, "risk_profile": "balanced", "currency": "KZT",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["feasibility"] == "UNREALISTIC"
    kinds = {row["kind"] for row in body["alternative_plans"]}
    assert {"INCREASE_CAPITAL", "ADD_MONTHLY_CONTRIBUTION", "EXTEND_HORIZON"} <= kinds
    # Staged reinvestment is the one lever that needs nothing extra from the user.
    assert "STAGED_REINVESTMENT" in kinds
    assert "слишком агрессивная" in body["warnings"][0]

    # Every alternative must be a real instruction. These were previously priced
    # at the profile's feasibility ceiling rather than at the return this plan
    # can actually deliver, so they came back advising "add 0 ₸ a month" and
    # "extend to the horizon you already chose".
    by_kind = {row["kind"]: row for row in body["alternative_plans"]}
    assert by_kind["INCREASE_CAPITAL"]["starting_capital"] > 500_000
    assert by_kind["INCREASE_CAPITAL"]["additional_capital"] > 0
    assert by_kind["ADD_MONTHLY_CONTRIBUTION"]["monthly_contribution"] > 0
    assert by_kind["EXTEND_HORIZON"]["horizon_months"] > 12
    assert by_kind["EXTEND_HORIZON"]["additional_months"] > 0


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
