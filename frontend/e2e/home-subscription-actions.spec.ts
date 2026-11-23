import { expect, test } from "@playwright/test";

for (const width of [390, 1440]) {
  test(`renewal leads the home actions after a VPN connection at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript(() => localStorage.setItem("rw_minishop_demo_language", "en"));
    await page.goto("/demo/runtime/home?mock=connected-subscription&theme_preview=dark");

    const stack = page.locator(".home-layout .action-stack");
    const actions = stack.getByRole("button");
    const renew = stack.locator(".subscription-renew-action");
    const premium = stack.locator('[data-webapp-action="open-premium-topup"]');
    const install = stack.getByRole("button", { name: "Install and configure", exact: true });
    await expect(actions).toHaveText([
      "Renew subscription",
      "Add traffic on Premium servers",
      "Install and configure",
      "Add traffic",
    ]);
    await expect(renew).toHaveClass(/\bbtn-primary\b/);
    await expect(premium).toHaveClass(/\bbtn-secondary\b/);
    await expect(install).toHaveClass(/\bbtn-secondary\b/);

    await renew.click();
    const payment = page.locator(".dialog-card.webapp-payment-dialog");
    await expect(payment).toBeVisible();
    await payment.locator(".dialog-close-button").click();
    await expect(payment).toBeHidden();

    await premium.click();
    const topup = page.locator(".dialog-card.webapp-topup-dialog");
    await expect(topup).toBeVisible();
    await topup.locator(".dialog-close-button").click();
    await expect(topup).toBeHidden();

    await install.click();
    await expect(page).toHaveURL(/\/install(?:[?#]|$)/);
  });
}

test("installation leads the home actions before a VPN connection is confirmed", async ({
  page,
}) => {
  await page.addInitScript(() => localStorage.setItem("rw_minishop_demo_language", "en"));
  await page.goto("/demo/runtime/home?mock=tariffs&theme_preview=dark");

  const stack = page.locator(".home-layout .action-stack");
  const first = stack.getByRole("button").first();
  await expect(first).toHaveText("Install and configure");
  await expect(first).toHaveClass(/\bbtn-primary\b/);
  await expect(stack.locator(".subscription-renew-action")).toHaveClass(/\bbtn-secondary\b/);
});
