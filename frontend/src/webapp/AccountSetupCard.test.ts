import { render } from "svelte/server";
import { describe, expect, it } from "vitest";

import AccountSetupCard from "./AccountSetupCard.svelte";
import type { NotificationPreferences } from "$lib/webapp/notificationPreferences.js";
import type { ApiClient } from "$lib/webapp/publicApi.js";

const preferences: NotificationPreferences = {
  marketing_email: true,
  marketing_telegram: true,
  system_email: true,
  system_telegram: true,
  push: true,
  push_notifications_enabled: true,
};

const api = (async () => ({ ok: true })) as ApiClient["api"];
const t = (key: string) => key;

function renderCard(surface: "home" | "settings"): string {
  return render(AccountSetupCard, {
    props: { api, preferences, t, surface },
  }).body;
}

describe("account setup card", () => {
  it("keeps web app installation and the existing combined Web Push opt-in together", () => {
    const html = renderCard("settings");
    expect(html).toContain("wa_setup_install_action");
    expect(html).toContain("wa_push_title");
    expect(html).toContain("wa_push_description");
    expect(html).toContain("wa_setup_install_note");
  });

  it("names both features in the Home shortcut while keeping the controls closed", () => {
    const html = renderCard("home");
    expect(html).toContain("wa_setup_short_title");
    expect(html).toContain("wa_setup_install_action");
    expect(html).toContain("wa_push_title");
    expect(html).not.toContain("wa_setup_install_note");
    expect(html).not.toContain("wa_push_description");
  });
});
