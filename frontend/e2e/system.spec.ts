import { expect, test } from "@playwright/test";

test("system page renders stored health telemetry", async ({ page }) => {
  await page.goto("/system");
  await expect(page.getByRole("heading", { name: "Состояние данных" })).toBeVisible();
  await expect(page.getByText("Цикл мониторинга")).toBeVisible();
  await expect(page.getByText("Последний запуск")).toBeVisible();
});

test("every subsystem reports a status derived from its own evidence", async ({ page }) => {
  await page.goto("/system");
  const panel = page.getByTestId("subsystems");
  await expect(panel).toBeVisible();

  // Phase 26 asks for one card per subsystem, each carrying a real status.
  for (const label of ["База данных", "Сборщик KASE", "Мониторинг", "Новости", "DCF",
                       "Технический анализ", "Парсер", "Планировщик"]) {
    await expect(panel.getByText(label, { exact: true })).toBeVisible();
  }

  // Nothing may be hard-coded green: a component with no evidence of having run
  // has to say so rather than inherit the application's status.
  const statuses = await panel.getByText(
    /^(работает|с ошибками|остановлена|не запускалась|выключена)$/
  ).allInnerTexts();
  expect(statuses).toHaveLength(8);
});
