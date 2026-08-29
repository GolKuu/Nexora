import { expect, test } from "@playwright/test";

const plan = {
  goal_id: 42, version: 1, methodology_version: "goal-planner-1.0.0", as_of: "2026-08-28",
  required_return: .1, required_return_pct: 10, feasibility: "FEASIBLE",
  target: { type: "FINAL_VALUE", amount: 5_500_000, planner_base_target: 5_665_000, safety_margin_percent: 3 },
  scenarios: {
    negative: { final_value: 5_200_000, target_reached: false, difference_vs_target: -300_000 },
    base: { final_value: 5_650_000, target_reached: true, difference_vs_target: 150_000 },
    positive: { final_value: 5_900_000, target_reached: true, difference_vs_target: 400_000 },
  },
  initial_portfolio: [{ instrument_id: 1, stock_id: 1, bond_id: null, ticker: "KZAP", name: "Казатомпром", instrument_type: "stock", issuer: "Казатомпром", currency: "KZT", quantity: 15, lot_size: 1, reference_price: 90_000, unit_cost: 90_000, purchase_cost: 1_350_000, allocation: .27, expected_return: .12, expected_contribution: .0324, risk: "Умеренный", liquidity: 82, score: 76, profile_match_score: 78, reason: "Проверяемая модель оценки." }],
  cash_remaining: 37_420,
  reinvestment_plan: [{ month: 3, available_before_purchase: 405_000, purchases: [{ ticker: "KZAP", quantity: 4, cost: 360_000 }], cash_remaining: 45_000 }],
  cashflow_calendar: Array.from({length:12},(_,index)=>({month:index+1,contribution:0,coupon:index===2?5000:0,dividend:0,principal:0,reinvested:index===2?360000:0,cash_balance:37420,dividend_basis:[]})),
  target_progress: { starting_capital: 5_000_000, contributions: 0, coupon_income: 120_000, dividend_income: 80_000, projected_market_gain: 450_000, projected_final_value: 5_650_000, target: 5_500_000, buffer_vs_target: 150_000 },
  warnings: ["Сценарии являются оценками."], alternative_plans: [{kind:"EXTEND_HORIZON",horizon_months:15}],
};

test("goal planner renders goal, downside, quantities and copies as planned", async ({ page }) => {
  await page.route(/\/api\/v1\/investment-goals\/plan$/, route => route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(plan)}));
  await page.route(/\/api\/v1\/investment-goals\/42\/copy-to-portfolio$/, route => route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({portfolio_id:7,positions_added:1,already_copied:false,status:"PLANNED"})}));
  await page.goto("/");
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("heading", {name:"План достижения инвестиционной цели"})).toBeVisible();
  const request = page.waitForRequest(request => request.url().includes("investment-goals/plan"));
  await page.getByRole("button", {name:"Построить план"}).click();
  await request;
  await expect(page.getByText("Негативный")).toBeVisible();
  await expect(page.getByLabel("Количество KZAP")).toHaveValue("15");
  await expect(page.getByText("Cash Remaining", {exact:false})).toHaveCount(0);
  await page.getByRole("button", {name:"Скопировать в портфель"}).click();
  await expect(page.getByText("Добавлено как PLANNED", {exact:false})).toBeVisible();
});
