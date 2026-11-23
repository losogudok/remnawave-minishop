import { expect, test } from "@playwright/test";
import type { InstallGuidesConfig } from "../src/lib/webapp/types";

function screenshotSource(color: string): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="420"><rect width="300" height="420" fill="${color}"/></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

for (const width of [1280, 390]) {
  for (const publicMode of [false, true]) {
    test(`install destinations select stable app IDs and follow history (${publicMode ? "public" : "account"}, ${width}px)`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      const path = publicMode
        ? `/s/${"b".repeat(32)}?`
        : "/demo/runtime/app/?mock=checkout-addons&path=/install&";
      await page.goto(`${path}platform=android&app=v2rayng`);
      await expect(page.locator(".install-platform-trigger")).toContainText("Android");
      await expect(page.locator(".install-app-button.active")).toContainText("v2rayNG");
      await page.screenshot({ path: testInfo.outputPath("targeted-install.png"), fullPage: true });
      await page.evaluate(() => {
        const url = new URL(window.location.href);
        url.searchParams.set("platform", "ios");
        url.searchParams.delete("app");
        window.history.pushState(null, "", url);
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      await expect(page.locator(".install-platform-trigger")).toContainText("iOS");
      await expect(page.locator(".install-app-button.active")).toContainText("RabbitHole");
      await page.goBack();
      await expect(page.locator(".install-platform-trigger")).toContainText("Android");
      await expect(page.locator(".install-app-button.active")).toContainText("v2rayNG");
      await page.locator(".install-app-button").filter({ hasText: "Happ" }).click();
      await expect(page.locator(".install-app-button.active")).toContainText("Happ");
      await page.evaluate(() => window.dispatchEvent(new PopStateEvent("popstate")));
      await expect(page.locator(".install-app-button.active")).toContainText("v2rayNG");
      expect(errors).toEqual([]);
    });

    test(`install screenshots follow the selected step (${publicMode ? "public" : "account"}, ${width}px)`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await page.route("**/subscription-guides-config.json", async (route) => {
        const response = await route.fetch();
        const config = (await response.json()) as InstallGuidesConfig;
        const apps = config.platforms?.android.apps ?? [];
        const happ = apps.find((app) => app.name === "Happ");
        const v2rayng = apps.find((app) => app.name === "v2rayNG");
        if (!happ || !v2rayng) throw new Error("Missing Android clients in demo fixture");
        happ.screenshots = [
          { src: screenshotSource("#2d9cff"), order: 3, captions: { ru: "Запуск VPN" } },
          { src: screenshotSource("#3ecf8e"), order: 2, captions: { ru: "Подписка" } },
          { src: screenshotSource("#68736f"), order: 1, captions: { ru: "Установка" } },
        ];
        v2rayng.screenshots = [{ src: screenshotSource("#68736f"), order: 1 }];
        await route.fulfill({ response, json: config });
      });

      const path = publicMode
        ? `/s/${"b".repeat(32)}?`
        : "/demo/runtime/app/?mock=checkout-addons&path=/install&";
      await page.goto(`${path}platform=android&app=happ`);
      const gallery = page.getByRole("region", { name: "Скриншоты и шаги" });
      const status = gallery.getByRole("status");
      const previous = gallery.getByRole("button", { name: "Предыдущий скриншот" });
      const next = gallery.getByRole("button", { name: "Следующий скриншот" });
      await expect(status).toHaveText("Шаг 1 из 3");
      await expect(previous).toBeDisabled();
      await expect(gallery.getByRole("button", { name: "1. Установка" })).toHaveAttribute(
        "aria-current",
        "step"
      );

      await next.click();
      await expect(status).toHaveText("Шаг 2 из 3");
      await expect(gallery.locator(".gallery-progress .filled")).toHaveCount(2);
      const activeSlide = gallery.getByRole("button", { name: "Открыть скриншот 2" });
      const viewport = gallery.locator(".gallery-viewport");
      const viewportBox = (await viewport.boundingBox())!;
      const slideBox = (await activeSlide.boundingBox())!;
      expect(
        Math.abs(slideBox.x + slideBox.width / 2 - viewportBox.x - viewportBox.width / 2)
      ).toBeLessThan(2);
      await expect(gallery).toHaveCSS("opacity", "1");
      await gallery.screenshot({ path: testInfo.outputPath("screenshot-steps.png") });

      await activeSlide.press("Enter");
      const viewer = page.getByRole("dialog", { name: "Подписка" });
      await viewer.getByRole("button", { name: "Увеличить", exact: true }).click();
      await expect(viewer.getByText("125%")).toBeVisible();
      await page.keyboard.press("Escape");
      await expect(viewer).toBeHidden();
      await expect(activeSlide).toBeFocused();

      await activeSlide.hover();
      await page.mouse.wheel(slideBox.width, 0);
      await expect(status).toHaveText("Шаг 3 из 3");
      await expect(next).toBeDisabled();
      await gallery.getByRole("button", { name: "1. Установка" }).click();
      await expect(status).toHaveText("Шаг 1 из 3");

      await page.locator(".install-app-button").filter({ hasText: "v2rayNG" }).click();
      await expect(status).toHaveText("Шаг 1 из 1");
      await expect(previous).toHaveCount(0);
      await expect(next).toHaveCount(0);
      await expect(gallery.getByRole("img", { name: "Скриншот 1" })).toBeVisible();
    });
  }

  test(`public install instructions recover after a preload error (${width}px)`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    const token = "a".repeat(32);
    await page.addInitScript((token) => {
      Object.assign(window, {
        __RW_PUBLIC_INSTALL_PRELOAD__: {
          path: `/subscription-guides/public/${token}`,
          promise: Promise.resolve({
            ok: true,
            enabled: false,
            config: null,
            error: "Panel service is unavailable",
          }),
        },
      });
    }, token);
    await page.goto(`/s/${token}`);
    const shell = page.locator(".public-install-shell");
    await expect(shell.getByText("Инструкции недоступны.", { exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("unavailable.png"), fullPage: true });
    await shell.getByRole("button", { name: "Загрузить снова", exact: true }).click();
    await expect(shell.locator(".install-empty")).toHaveCount(0);
    await expect(
      shell.getByRole("button", { name: "Добавить подписку", exact: true })
    ).toBeVisible();
    await expect(shell.locator(".install-step-motion").last()).toHaveCSS("opacity", "1");
    await expect(page).toHaveURL(new RegExp(`/s/${token}$`));
    await page.screenshot({ path: testInfo.outputPath("recovered.png"), fullPage: true });
  });
}
