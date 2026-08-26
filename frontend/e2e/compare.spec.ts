import { expect, test } from "@playwright/test";

test("two real stocks are compared in one user action", async ({ page }) => {
  await page.goto("/compare");
  await page.getByRole("button", { name: "Сравнить", exact: true }).first().click();
  await expect(page.getByRole("columnheader", { name: "HSBK" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "KCEL" })).toBeVisible();
});
