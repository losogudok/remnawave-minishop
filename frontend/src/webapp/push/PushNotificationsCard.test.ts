import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import PushNotificationsCard from "./PushNotificationsCard.svelte";
import type { WebPushStatus } from "$lib/webapp/webPush.js";
import type { NotificationPreferences } from "$lib/webapp/notificationPreferences.js";

const preferences: NotificationPreferences = {
  marketing_email: true,
  marketing_telegram: true,
  system_email: true,
  system_telegram: true,
  push: true,
  push_notifications_enabled: true,
};

const configuredStatus: WebPushStatus = {
  configured: true,
  pwa_enabled: true,
  public_key: "public-key",
  preference_enabled: true,
  devices: [
    {
      id: "device-one",
      label: "Pixel",
      current: true,
      created_at: "2026-11-05T12:00:00Z",
      last_seen_at: "2026-11-05T12:01:00Z",
    },
  ],
  current_device_id: "device-one",
};

function renderCard(
  initialState:
    | "unsupported"
    | "insecure"
    | "embedded"
    | "denied"
    | "not_configured"
    | "pwa_disabled"
    | "ios_install"
    | "no_service_worker"
    | "default"
    | "disabled"
    | "enabled",
  status: WebPushStatus | null = configuredStatus,
  overrides: Record<string, unknown> = {}
) {
  return render(PushNotificationsCard, {
    props: {
      preferences,
      initialStatus: status,
      initialState,
      ...overrides,
    },
  }).body;
}

describe("PushNotificationsCard state rendering", () => {
  it("explains unsupported, insecure and embedded browsers without an enable button", () => {
    for (const [state, copy] of [
      ["unsupported", "wa_push_unsupported"],
      ["insecure", "wa_push_insecure"],
      ["embedded", "wa_push_embedded"],
    ] as const) {
      const html = renderCard(state);
      expect(html).toContain(copy);
      expect(html).not.toContain("wa_push_enable");
    }
  });

  it("guides denied permission, disabled PWA, missing worker and iOS installation", () => {
    expect(renderCard("denied")).toContain("wa_push_denied");
    expect(renderCard("pwa_disabled")).toContain("wa_push_pwa_disabled");
    expect(renderCard("no_service_worker")).toContain("wa_push_no_service_worker");
    expect(renderCard("ios_install")).toContain("wa_push_ios_install");
  });

  it("keeps remote devices and account opt-out available in unsupported browsers", () => {
    const html = renderCard("unsupported", configuredStatus);
    expect(html).toContain("wa_push_unsupported");
    expect(html).toContain("Pixel");
    expect(html).toContain("wa_push_devices_title");
    expect(html).toContain("wa_push_disable");
    expect(html).toContain("wa_push_device_remove");
  });

  it("shows not-configured guidance without a dead action", () => {
    const html = renderCard("not_configured", {
      ...configuredStatus,
      configured: false,
      public_key: "",
    });
    expect(html).toContain("wa_push_not_configured");
    expect(html).not.toContain("wa_push_enable");
  });

  it("offers the explicit enable action only when the server is configured", () => {
    expect(renderCard("default")).toContain("wa_push_enable");
    expect(renderCard("disabled")).toContain("wa_push_enable");
    const notConfigured = renderCard("default", {
      ...configuredStatus,
      configured: false,
      public_key: "",
    });
    expect(notConfigured).not.toContain("wa_push_enable");
  });

  it.each(["revoked", "expired", "unknown"])(
    "never labels a locally subscribed but server-unconfirmed %s device Active",
    () => {
      const html = renderCard("enabled", {
        ...configuredStatus,
        current_device_id: "",
        devices: [],
      });
      expect(html).not.toContain("wa_push_enabled_status");
      expect(html).toContain("wa_push_not_enabled");
      expect(html).toContain("wa_push_enable");
    }
  );

  it("lists devices and marks the current browser when enabled", () => {
    const html = renderCard("enabled");
    expect(html).toContain("wa_push_enabled_status");
    expect(html).toContain("Pixel");
    expect(html).toContain("wa_push_current_device");
    expect(html).toContain("wa_push_disable");
  });
});
