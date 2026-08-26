import { expect, test } from "@playwright/test";

test("home renders the primary flow without waiting for a collector", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByPlaceholder("Введите тикер, например KZAP")).toBeVisible();
  await expect(page.getByRole("button", { name: "Проанализировать" })).toBeDisabled();
});
