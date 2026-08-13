import { expect, test } from "@playwright/test";

test("broadcast editor is compact and history uses masonry columns on desktop", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/demo/runtime/admin/broadcast?theme_preview=dark");

  await expect(page.getByRole("heading", { name: "История рассылок" })).toBeVisible();
  const controls = page.locator(".broadcast-control-panel");
  await expect(controls).toHaveCount(4);
  const controlBoxes = await controls.evaluateAll((elements) =>
    elements.map((element) => element.getBoundingClientRect())
  );
  expect(new Set(controlBoxes.map((box) => Math.round(box.top))).size).toBe(1);

  const cards = page.locator(".broadcast-history-card");
  await expect(cards).toHaveCount(3);
  await cards.evaluateAll(async (elements) => {
    await Promise.all(
      elements.flatMap((element) => element.getAnimations().map((item) => item.finished))
    );
  });
  const cardBoxes = await cards.evaluateAll((elements) =>
    elements.map((element) => element.getBoundingClientRect())
  );
  expect(new Set(cardBoxes.map((box) => Math.round(box.left))).size).toBeGreaterThan(1);
  expect(new Set(cardBoxes.map((box) => Math.round(box.height))).size).toBeGreaterThan(1);
});

test("broadcast history stacks on mobile and scheduled cards can be edited and removed", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto("/demo/runtime/admin/broadcast?theme_preview=dark");

  const controls = page.locator(".broadcast-control-panel");
  await expect(controls).toHaveCount(4);
  const controlBoxes = await controls.evaluateAll((elements) =>
    elements.map((element) => element.getBoundingClientRect())
  );
  expect(new Set(controlBoxes.map((box) => Math.round(box.left))).size).toBe(1);
  expect(new Set(controlBoxes.map((box) => Math.round(box.top))).size).toBe(4);

  const cards = page.locator(".broadcast-history-card");
  await expect(cards).toHaveCount(3);
  const cardBoxes = await cards.evaluateAll((elements) =>
    elements.map((element) => element.getBoundingClientRect())
  );
  expect(new Set(cardBoxes.map((box) => Math.round(box.left))).size).toBe(1);

  const scheduledCard = cards.filter({ hasText: "Запланирована" }).first();
  await scheduledCard.getByRole("button", { name: "Изменить время" }).click();
  const scheduleInput = scheduledCard.locator('input[type="datetime-local"]');
  await expect(scheduleInput).toBeVisible();
  await scheduleInput.fill("2031-05-20T14:30");
  await scheduledCard.getByRole("button", { name: "Обновить" }).click();
  await expect(scheduledCard).toContainText("20.05.2031");

  page.once("dialog", (dialog) => dialog.accept());
  await scheduledCard.getByRole("button", { name: "Отменить и удалить" }).click();
  await expect(cards).toHaveCount(2);
});
