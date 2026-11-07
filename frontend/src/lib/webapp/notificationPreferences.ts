export type NotificationPreferences = {
  marketing_email: boolean;
  marketing_telegram: boolean;
  system_email: boolean;
  system_telegram: boolean;
  /** Legacy short key still returned by the shared preference payload. */
  push: boolean;
  push_notifications_enabled: boolean;
};

export const DEFAULT_NOTIFICATION_PREFERENCES: NotificationPreferences = {
  marketing_email: true,
  marketing_telegram: true,
  system_email: true,
  system_telegram: true,
  push: true,
  push_notifications_enabled: true,
};

function pushPreference(record: Record<string, unknown>): boolean {
  if (typeof record.push_notifications_enabled === "boolean") {
    return record.push_notifications_enabled;
  }
  // Older payloads used the short `push` key; keep them working while the
  // new preference name rolls out.
  if (typeof record.push === "boolean") return record.push;
  return true;
}

export function normalizeNotificationPreferences(value: unknown): NotificationPreferences {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const push = pushPreference(record);
  return {
    marketing_email: record.marketing_email !== false,
    marketing_telegram: record.marketing_telegram !== false,
    system_email: record.system_email !== false,
    system_telegram: record.system_telegram !== false,
    push,
    push_notifications_enabled: push,
  };
}

export function notificationPreferencesPayload(
  preferences: NotificationPreferences
): Record<string, boolean> {
  return {
    marketing_email: preferences.marketing_email,
    marketing_telegram: preferences.marketing_telegram,
    system_email: preferences.system_email,
    system_telegram: preferences.system_telegram,
    push_notifications_enabled: preferences.push_notifications_enabled,
  };
}
