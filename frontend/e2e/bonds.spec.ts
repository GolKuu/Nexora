import { expect, test } from "@playwright/test";

test("bonds shows a non-empty stored catalog", async ({ page }) => {
  const responsePromise = page.waitForResponse((response) =>
    response.url().includes("/api/v1/bonds?limit=200"),
  );
  await page.goto("/bonds");
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const payload = await response.json();
  expect(payload.total).toBeGreaterThan(0);
  await expect(page.getByText(/\d+ из \d+ · YTM/)).toBeVisible();
});
