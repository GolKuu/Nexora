import { expect, test } from "@playwright/test";

test("system page renders stored health telemetry", async ({ page }) => {
  await page.goto("/system");
  await expect(page.getByRole("heading", { name: "Состояние данных" })).toBeVisible();
  await expect(page.getByText("База данных")).toBeVisible();
  await expect(page.getByText("Цикл мониторинга")).toBeVisible();
  await expect(page.getByText("Последний запуск")).toBeVisible();
});
