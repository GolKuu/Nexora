import { expect, test } from "@playwright/test";

test("stocks shows a non-empty stored catalog", async ({ page }) => {
  const responsePromise = page.waitForResponse((response) =>
    response.url().includes("/api/v1/stocks?limit=40"),
  );
  await page.goto("/stocks");
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const payload = await response.json();
  expect(payload.total).toBeGreaterThan(0);
  await expect(page.getByText(/\d+ из \d+ · цена/)).toBeVisible();
  await expect(page.getByText("0 из 0", { exact: false })).toHaveCount(0);
});

test("stocks never converts a backend failure into 0 из 0", async ({ page }) => {
  await page.route("**/api/v1/stocks?limit=40", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ error: { code: "unavailable", message: "backend unavailable" } }),
  }));
  await page.goto("/stocks");
  await expect(page.getByText("Не удалось загрузить список акций").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Повторить" })).toBeVisible();
  await expect(page.getByText("0 из 0", { exact: false })).toHaveCount(0);
});
