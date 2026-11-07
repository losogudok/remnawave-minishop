import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`home install card persists until confirmed installation at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 950 });
    await page.route(/\/demo\/runtime\/home(?:\?.*)?$/, async (route) => {
      const response = await route.fetch();
      const html = await response.text();
      await route.fulfill({
        response,
        body: html.replace(
          "</head>",
          '<link rel="manifest" href="data:application/manifest+json,%7B%7D"></head>'
        ),
      });
    });
    await page.addInitScript(() => {
      const key = "remnawave:pwa-install-offer:v1";
      if (!localStorage.getItem(key)) {
        localStorage.setItem(key, JSON.stringify({ shownAt: Date.now(), outcome: "dismissed" }));
      }
    });
    await page.goto("/demo/runtime/home?theme_preview=dark");
    const offer = page.locator(".home-bottom .pwa-install-offer");
    await expect(offer).toBeVisible();
    await page.reload();
    await expect(offer).toBeVisible();
    await page.evaluate(() => {
      const event = new Event("beforeinstallprompt", { cancelable: true });
      Object.assign(event, {
        prompt: () => Promise.resolve(),
        userChoice: Promise.resolve({ outcome: "accepted" }),
      });
      window.dispatchEvent(event);
    });
    await offer.getByRole("button").click();
    await page.locator(".pwa-install-dialog .pwa-install-action").click();
    await page.locator(".pwa-install-dialog .dialog-close-button").click();
    await expect(offer).toBeVisible();
    await page.evaluate(() => window.dispatchEvent(new Event("appinstalled")));
    await expect(offer).toHaveCount(0);
    await page.reload();
    await expect(offer).toHaveCount(0);
  });
}
