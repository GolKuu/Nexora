import { expect, test } from "@playwright/test";

test("settings persist across reload for an anonymous user", async ({ page }) => {
  await page.goto("/settings");
  const currency = page.getByLabel("Базовая валюта");
  await currency.selectOption("USD");
  await expect(currency).toHaveValue("USD");
  await page.reload();
  await expect(page.getByLabel("Базовая валюта")).toHaveValue("USD");
});
