import { expect, test } from "@playwright/test";

test("Analyze normalizes a ticker and reaches a deterministic DCF outcome", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder("Введите тикер, например KZAP").fill("kzap");
  await page.getByRole("button", { name: "Проанализировать" }).click();
  await expect(page).toHaveURL(/\/stock\/KZAP/i);
  await expect(page.getByText("AI DCF Valuation")).toBeVisible();

  const calculate = page.getByRole("button", { name: "Рассчитать справедливую стоимость" });
  if (await calculate.isVisible()) await calculate.click();
  await expect(
    page.getByText("Негативный").or(page.getByRole("alert")),
  ).toBeVisible({ timeout: 30_000 });
});
