import { readFileSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const en = JSON.parse(readFileSync("../locales/en.json", "utf8"));
const endpoint = "https://fcm.googleapis.com/fcm/send/local-device";

async function configure(page: Page, failPreference = false) {
  let active = false;
  let preference = false;
  const calls: string[] = [];
  await page.addInitScript((endpoint) => {
    const subscription = {
      endpoint,
      options: { applicationServerKey: new Uint8Array([1, 2, 3]).buffer },
      toJSON: () => ({ endpoint, keys: { p256dh: "p", auth: "a" } }),
      unsubscribe: async () => {
        throw new Error("browser unsubscribe failed");
      },
    };
    const registration = {
      pushManager: {
        getSubscription: async () => subscription,
        subscribe: async () => subscription,
      },
    };
    Object.defineProperty(navigator, "serviceWorker", {
      value: {
        getRegistration: async () => registration,
        ready: Promise.resolve(registration),
      },
    });
    Object.defineProperty(Notification, "permission", { get: () => "granted" });
  }, endpoint);
  await page.route("**/demo/runtime/**", async (route) => {
    if (route.request().resourceType() !== "document") return route.continue();
    const response = await route.fetch();
    const config = { authProviders: ["email"], defaultLanguage: "en" };
    const body = (await response.text()).replace(
      "</head>",
      `<link rel="manifest" href="/manifest.webmanifest"><script id="webapp-config" type="application/json">${JSON.stringify(config)}</script></head>`
    );
    await route.fulfill({ response, body });
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    let response: Record<string, unknown> = { ok: true };
    if (path === "/api/auth/session")
      response = { ok: true, authenticated: true, csrf_token: "test" };
    if (path.startsWith("/api/i18n")) response = { ok: true, i18n: { en } };
    if (path === "/api/me")
      response = {
        ok: true,
        user: {
          id: 42,
          language_code: "en",
          notification_preferences: { push_notifications_enabled: preference },
        },
        settings: {},
        subscription: { active: false },
        plans: [],
        payment_methods: [],
        referral: {},
      };
    if (path === "/api/extensions/runtime") response = { ok: true, generation: 1, plugins: [] };
    if (path.startsWith("/api/plugins/web_push/")) {
      calls.push(`${method} ${path}`);
      if (path === "/api/plugins/web_push/subscription") active = method === "POST";
      response = {
        ok: true,
        configured: true,
        pwa_enabled: true,
        public_key: "AQID",
        preference_enabled: preference,
        current_device_id: active ? "local-device" : "",
        devices: active ? [{ id: "local-device", current: true, label: "Local browser" }] : [],
      };
    }
    if (path === "/api/account/notification-preferences") {
      const next = route.request().postDataJSON();
      preference = next.push_notifications_enabled;
      // Fail only the opt-in write; the compensation write back to false may
      // succeed and must be able to restore the prior preference.
      response = {
        ok: !failPreference || !next.push_notifications_enabled,
        notification_preferences: next,
      };
    }
    await route.fulfill({ json: response });
  });
  return calls;
}

for (const width of [1440, 390]) {
  test(`unconfirmed browser subscription stays disabled after reload at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 });
    const calls = await configure(page);
    await page.goto("/demo/runtime/settings/notifications?theme_preview=dark");
    const card = page.locator(".push-notifications-card");
    await expect(card).toContainText(en.wa_push_not_enabled);
    await expect(card).not.toContainText(en.wa_push_enabled_status);
    await page.reload();
    await expect(card).toContainText(en.wa_push_not_enabled);
    expect(calls).toContain("POST /api/plugins/web_push/subscription/sync");
    expect(calls).not.toContain("POST /api/plugins/web_push/subscription");
    expect(await card.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(
      2
    );
  });

  test(`failed enrollment revokes current endpoint at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const calls = await configure(page, true);
    await page.goto("/demo/runtime/settings/notifications?theme_preview=dark");
    const card = page.locator(".push-notifications-card");
    await card.getByRole("button", { name: en.wa_push_enable, exact: true }).click();
    await expect(card).toContainText(en.wa_push_enable_failed);
    expect(calls).toContain("POST /api/plugins/web_push/subscription");
    expect(calls).toContain("DELETE /api/plugins/web_push/subscription");
    await expect(card).not.toContainText(en.wa_push_enabled_status);
  });
}
