import { expect, test } from "@playwright/test";

test("a real stock can be added to and resolved from the watchlist", async ({ page }) => {
  await page.goto("/stock/HSBK");
  const watchButton = page.getByRole("button", { name: /В избранное/ });
  if (await watchButton.getAttribute("aria-pressed") !== "true") await watchButton.click();
  await page.goto("/watchlist");
  await expect(page.getByRole("link", { name: /HSBK/ }).first()).toBeVisible();
});
