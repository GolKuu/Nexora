import { expect, test } from "@playwright/test";

test("universal search resolves a real ticker", async ({ page }) => {
  await page.goto("/");
  await page.getByPlaceholder("Тикер, ISIN или компания — акция или облигация").fill("HSBK");
  await expect(page.getByRole("link", { name: /HSBK/ }).first()).toBeVisible();
});
