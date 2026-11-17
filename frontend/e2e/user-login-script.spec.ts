import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`user login script copies from the card at ${width}px`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 900 });
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/demo/runtime/admin/users/910001?theme_preview=dark");
    const button = page.getByRole("button", { name: "Войти под пользователем", exact: true });
    await expect(button).toBeVisible();
    await expect(button).toBeEnabled();
    const adminUrl = page.url();
    await button.click();
    await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("void 0;");
    await expect(page).toHaveURL(adminUrl);
    const bounds = await button.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width);
    expect(errors).toEqual([]);
    await page.screenshot({ path: `/tmp/minishop-login-card-${width}.png`, fullPage: true });
  });
}
