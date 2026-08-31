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

test("bonds never converts a backend failure into 0 из 0", async ({ page }) => {
  await page.route("**/api/v1/bonds?limit=200", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ error: { code: "unavailable", message: "backend unavailable" } }),
  }));
  await page.goto("/bonds");
  await expect(page.getByText("Не удалось загрузить список облигаций").first()).toBeVisible();
  await expect(page.getByText("0 из 0", { exact: false })).toHaveCount(0);
});

test("a catalogue that has not loaded yet never claims a count", async ({ page }) => {
  // Hold the response open: with no data, no error and SWR no longer reporting
  // `isLoading`, the header used to render a confident "0 из 0".
  await page.route("**/api/v1/bonds?limit=200", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 4000));
    await route.abort();
  });
  await page.goto("/bonds");
  await expect(page.getByText("0 из 0", { exact: false })).toHaveCount(0);
});
