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

  test(`user Web Push and connection screenshots at ${width}px`, async ({ page }) => {
    test.setTimeout(30_000);
    await page.setViewportSize({ width, height: 950 });
    await page.addInitScript(() => {
      type Api = (path: string, options?: RequestInit) => Promise<Record<string, unknown>>;
      type Bundle = { mount: (target: HTMLElement, props: { api: Api }) => unknown };
      let bundle: Bundle | undefined;
      Object.defineProperty(window, "SubscriptionWebAppAdmin", {
        configurable: true,
        get: () => bundle,
        set(value: Bundle) {
          const mount = value.mount;
          value.mount = (target, props) => {
            const api: Api = async (path, options) => {
              if (path === "/api/admin/guide-media/galleries") {
                const key = { config_id: "file:default", platform: "android", client_id: "happ" };
                return {
                  ok: true,
                  catalog_available: true,
                  orphaned_known: true,
                  orphaned: [],
                  catalog: [
                    {
                      config_id: key.config_id,
                      source: "file",
                      platforms: [
                        { id: "android", label: "Android", apps: [{ id: "happ", name: "Happ" }] },
                      ],
                    },
                  ],
                  galleries: [
                    {
                      ...key,
                      orphaned: false,
                      images: [1, 2].map((index) => ({
                        ...key,
                        image_id: String(index),
                        digest: String(index),
                        captions: { ru: "Шаг " + index, en: "Step " + index },
                        order: index,
                        content_type: "image/png",
                        width: 1,
                        height: 1,
                        size_bytes: 100,
                        src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
                      })),
                    },
                  ],
                };
              }
              if (
                path.startsWith("/api/admin/guide-media/images/") &&
                options?.method === "PATCH"
              ) {
                return {
                  ok: true,
                  image: {
                    config_id: "file:default",
                    platform: "android",
                    client_id: "happ",
                    image_id: path.split("/").at(-1),
                    digest: "1",
                    captions: { ru: "Шаг 1", en: "Step 1" },
                    order: 1,
                    content_type: "image/png",
                    width: 1,
                    height: 1,
                    size_bytes: 100,
                    src: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
                  },
                };
              }
              if (path === "/admin/broadcast" && options?.method === "POST") {
                const body = JSON.parse(String(options.body)) as {
                  target: string;
                  channels: string[];
                };
                sessionStorage.setItem("test:direct-message", JSON.stringify(body));
              }
              const response = await props.api(path, options);
              if (path !== "/admin/broadcast/audience-counts") return response;
              return {
                ...response,
                channels: [
                  ...(response.channels as unknown[]),
                  {
                    name: "push",
                    label_key: "broadcast_channel_push",
                    fallback_label: "Web Push",
                    available: true,
                    supports_html: false,
                    supports_subject: false,
                    supports_image: false,
                    supports_buttons: false,
                    supports_push_preview: true,
                    destination_level: "endpoint",
                  },
                ],
              };
            };
            return mount(target, { ...props, api });
          };
          bundle = value;
        },
      });
    });
    await page.goto("/demo/runtime/admin/users/910001?theme_preview=dark");
    await page.locator('.admin-tabs-trigger[data-value="message"]').first().click();
    const composer = page.locator(".admin-user-action-sheet--message");
    const push = composer.locator("#user-message-channel-push");
    await expect(push).toBeEnabled();
    for (const checkbox of await composer
      .locator(".admin-user-message-channels")
      .getByRole("checkbox")
      .all()) {
      if ((await checkbox.getAttribute("aria-checked")) === "true") await checkbox.click();
    }
    await push.click();
    await composer.locator(".ProseMirror").fill("Personal push test");
    await composer.locator('[data-admin-action="send-user-message"]').click();
    await expect
      .poll(() =>
        page.evaluate(() => JSON.parse(sessionStorage.getItem("test:direct-message") || "{}"))
      )
      .toMatchObject({
        target: "user:910001",
        channels: ["push"],
      });
    await page.goto(
      "/demo/runtime/admin/settings/subscription-guides/screenshots?theme_preview=dark"
    );
    const subsection = page
      .locator(".admin-settings-subsection")
      .filter({ has: page.locator(".guide-media") });
    await expect(subsection.locator(".guide-media-card")).toHaveCount(2);
    const card = subsection.locator(".guide-media-card").first();
    const save = card.getByRole("button", { name: "Сохранить", exact: true });
    const remove = card.getByRole("button", { name: "Удалить", exact: true });
    const [saveBox, removeBox] = await Promise.all([save.boundingBox(), remove.boundingBox()]);
    expect(saveBox).toBeTruthy();
    expect(removeBox).toBeTruthy();
    expect(Math.abs(saveBox!.y - removeBox!.y)).toBeLessThan(1);
    expect(Math.abs(saveBox!.height - removeBox!.height)).toBeLessThan(1);
    expect(removeBox!.x).toBeGreaterThan(saveBox!.x);
    await save.click();
    await remove.click();
    await expect(page.locator(".guide-media-delete-dialog")).toBeVisible();
    await page.locator(".guide-media-delete-dialog button").first().focus();
    await page.locator(".guide-media-delete-dialog button").first().press("Escape");
    await expect(page.locator(".guide-media-delete-dialog")).toHaveCount(0);
    await card.locator(".guide-media-thumb").click();
    await expect(page.locator("[data-image-viewer]")).toBeVisible();
    const viewerClose = page.locator('[data-image-viewer-action="close"]');
    await viewerClose.focus();
    await viewerClose.press("Escape");
    await expect(page.locator("[data-image-viewer]")).toHaveCount(0);
    await subsection.locator(".admin-select-trigger").first().click();
    await expect(page.getByRole("option", { name: /file:default/ })).toBeVisible();
    await page.keyboard.press("Escape");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)
    ).toBeLessThanOrEqual(1);
  });
}
