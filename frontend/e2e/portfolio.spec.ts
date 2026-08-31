import { expect, test } from "@playwright/test";

test("anonymous user can create a portfolio and add a stock position", async ({ page }) => {
  await page.goto("/portfolio");

  // The portfolio list resolves asynchronously. `isVisible()` does not wait, so
  // checking it while the skeleton is still up silently skipped the click and
  // then failed on the form that never opened. Wait for whichever state the
  // page settles into first.
  const create = page.getByRole("button", { name: "Создать портфель" });
  const addForm = page.getByText("Добавить позицию");
  await expect(create.or(addForm).first()).toBeVisible();

  if (await create.isVisible()) await create.click();
  await expect(addForm).toBeVisible();

  await page.getByLabel("Тип инструмента").selectOption("stock");
  await page.getByLabel("Тикер или ISIN").fill("HSBK");
  await page.getByLabel("Количество").fill("2");
  await page.getByRole("button", { name: "Добавить", exact: true }).click();
  await expect(page.getByRole("link", { name: "HSBK" })).toBeVisible();
});
