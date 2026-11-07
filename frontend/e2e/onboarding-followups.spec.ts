import { expect, test } from "@playwright/test";

for (const width of [1440, 390]) {
  test(`account setup stays optional and available in Settings at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 950 });
    await page.addInitScript(() => {
      if (localStorage.getItem("test:pwa-enabled") !== "1") return;
      const addManifest = () => {
        const root = document.documentElement;
        if (!root) return;
        let head = document.head;
        if (!head) {
          head = document.createElement("head");
          root.insertBefore(head, root.firstChild);
        }
        if (head.querySelector("#app-manifest")) return;
        const manifest = document.createElement("link");
        manifest.id = "app-manifest";
        manifest.rel = "manifest";
        manifest.href = "data:application/manifest+json,%7B%7D";
        head.append(manifest);
      };
      addManifest();
      const observer = new MutationObserver(() => {
        addManifest();
        if (document.querySelector("#app-manifest")) observer.disconnect();
      });
      observer.observe(document, { childList: true, subtree: true });
    });
    await page.goto(
      "/demo/runtime/app/?path=/home&mock=balance-recurring-methods&theme_preview=dark"
    );
    const card = page.locator(".account-setup-card");
    await expect(card).toBeVisible();
    await expect(card.locator(".setup-offer")).toHaveCount(0);
    await expect(card.locator(".push-notifications-card")).toBeVisible();
    await expect(page.locator(".pwa-install-dialog")).toHaveCount(0);

    await page.locator('[data-webapp-action="setup-install"]').click();
    const dialog = page.locator(".pwa-install-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(".pwa-install-state")).toContainText("не поддерживает");
    await dialog.locator(".dialog-close-button").click();
    await expect(dialog).toHaveCount(0);

    await page.evaluate(() => localStorage.setItem("test:pwa-enabled", "1"));
    await page.reload();
    await expect(page.locator("#app-manifest")).toHaveCount(1);
    await page.evaluate(() => {
      const event = new Event("beforeinstallprompt", { cancelable: true });
      Object.defineProperty(event, "prompt", { value: async () => {} });
      Object.defineProperty(event, "userChoice", {
        value: Promise.resolve({ outcome: "accepted" }),
      });
      window.dispatchEvent(event);
    });
    await expect(card.locator(".setup-offer")).toContainText("5");
    await expect(page.locator(".pwa-install-dialog")).toHaveCount(0);
    await page.locator('[data-webapp-action="setup-install"]').click();
    await expect(dialog.locator(".pwa-install-action")).toBeVisible();
    await dialog.locator(".pwa-install-action").click();
    await expect(dialog.locator(".pwa-install-status")).toBeVisible();
    await dialog.locator(".dialog-close-button").click();
    await expect(dialog).toHaveCount(0);

    await page.locator('[data-webapp-action="setup-dismiss"]').click();
    await expect(card).toHaveCount(0);
    await page.reload();
    await expect(card).toHaveCount(0);

    await page.goto(
      "/demo/runtime/app/?path=/settings&mock=balance-recurring-methods&theme_preview=dark"
    );
    const settingsCard = page.locator(".account-setup-card");
    await expect(settingsCard).toBeVisible();
    await expect(settingsCard.locator(".setup-offer")).toContainText("5");
    await expect(settingsCard.locator(".push-notifications-card")).toBeVisible();
    await expect(settingsCard.locator('[data-webapp-action="setup-dismiss"]')).toHaveCount(0);
    await expect(page.locator(".pwa-install-dialog")).toHaveCount(0);
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
