import { expect, test } from "@playwright/test";

test("anonymous user can create a portfolio and add a stock position", async ({ page }) => {
  await page.goto("/portfolio");
  const create = page.getByRole("button", { name: "Создать портфель" });
  if (await create.isVisible()) await create.click();
  await expect(page.getByText("Добавить позицию")).toBeVisible();
  await page.getByLabel("Тип инструмента").selectOption("stock");
  await page.getByLabel("Тикер или ISIN").fill("HSBK");
  await page.getByLabel("Количество").fill("2");
  await page.getByRole("button", { name: "Добавить", exact: true }).click();
  await expect(page.getByRole("link", { name: "HSBK" })).toBeVisible();
});
