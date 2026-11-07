import { describe, expect, it } from "vitest";

import {
  DEFAULT_NOTIFICATION_PREFERENCES,
  normalizeNotificationPreferences,
  notificationPreferencesPayload,
} from "./notificationPreferences.js";

describe("notificationPreferences", () => {
  it("defaults the single browser push opt-in to enabled", () => {
    expect(DEFAULT_NOTIFICATION_PREFERENCES.push_notifications_enabled).toBe(true);
    expect(normalizeNotificationPreferences(null)).toEqual(DEFAULT_NOTIFICATION_PREFERENCES);
  });

  it("normalizes the new push field and the legacy short key", () => {
    expect(
      normalizeNotificationPreferences({
        marketing_email: false,
        push_notifications_enabled: false,
      })
    ).toMatchObject({
      marketing_email: false,
      marketing_telegram: true,
      system_email: true,
      system_telegram: true,
      push_notifications_enabled: false,
    });
    expect(normalizeNotificationPreferences({ push: false }).push_notifications_enabled).toBe(
      false
    );
    expect(
      normalizeNotificationPreferences({ push_notifications_enabled: true, push: false })
        .push_notifications_enabled
    ).toBe(true);
  });

  it("serializes one payload without dropping Telegram or email choices", () => {
    expect(
      notificationPreferencesPayload({
        marketing_email: false,
        marketing_telegram: true,
        system_email: false,
        system_telegram: true,
        push: false,
        push_notifications_enabled: false,
      })
    ).toEqual({
      marketing_email: false,
      marketing_telegram: true,
      system_email: false,
      system_telegram: true,
      push_notifications_enabled: false,
    });
  });
});
