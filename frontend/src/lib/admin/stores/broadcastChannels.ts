/** Broadcast channel descriptors and selection helpers shared by the store. */

export type BroadcastChannelDescriptor = {
  name: string;
  labelKey: string;
  fallbackLabel: string;
  order: number;
  available: boolean;
  reason: string;
  supportsHtml: boolean;
  supportsSubject: boolean;
  supportsImage: boolean;
  supportsButtons: boolean;
  maxTextLength: number;
  supportsPushPreview: boolean;
  destinationLevel: "user" | "endpoint";
};

export const LEGACY_CHANNEL_ORDER: Record<string, number> = { telegram: 10, email: 20 };

export function asBroadcastChannels(value: unknown): BroadcastChannelDescriptor[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value
    .map((item) => {
      const raw = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
      const destinationLevel = raw.destination_level === "endpoint" ? "endpoint" : "user";
      return {
        name: String(raw.name || "")
          .trim()
          .toLowerCase(),
        labelKey: String(raw.label_key || "").trim(),
        fallbackLabel: String(raw.fallback_label || "").trim(),
        order: Number.isFinite(Number(raw.order)) ? Number(raw.order) : 100,
        available: raw.available !== false,
        reason: String(raw.reason || "").trim(),
        supportsHtml: raw.supports_html !== false,
        supportsSubject: raw.supports_subject === true,
        supportsImage: raw.supports_image !== false,
        supportsButtons: raw.supports_buttons !== false,
        maxTextLength: Number.isFinite(Number(raw.max_text_length))
          ? Math.max(0, Number(raw.max_text_length))
          : 4096,
        supportsPushPreview: raw.supports_push_preview === true,
        destinationLevel,
      } satisfies BroadcastChannelDescriptor;
    })
    .filter((item) => {
      if (!item.name || !item.labelKey || !item.fallbackLabel || seen.has(item.name)) {
        return false;
      }
      seen.add(item.name);
      return true;
    })
    .sort((left, right) => left.order - right.order || left.name.localeCompare(right.name));
}

export function defaultBroadcastChannels(emailAvailable = true): BroadcastChannelDescriptor[] {
  return [
    {
      name: "telegram",
      labelKey: "broadcast_channel_telegram",
      fallbackLabel: "Telegram",
      order: 10,
      available: true,
      reason: "",
      supportsHtml: true,
      supportsSubject: false,
      supportsImage: true,
      supportsButtons: true,
      maxTextLength: 4096,
      supportsPushPreview: false,
      destinationLevel: "user",
    },
    {
      name: "email",
      labelKey: "broadcast_channel_email",
      fallbackLabel: "Email",
      order: 20,
      available: emailAvailable,
      reason: emailAvailable ? "" : "email_not_configured",
      supportsHtml: true,
      supportsSubject: true,
      supportsImage: true,
      supportsButtons: true,
      maxTextLength: 0,
      supportsPushPreview: false,
      destinationLevel: "user",
    },
  ];
}

/**
 * A hard length limit is one that rejects the draft. Push-preview channels
 * shorten their own payload, so their advertised title+body budget must not
 * block a mixed campaign that another channel can deliver in full.
 */
export function hardTextLimit(
  channels: BroadcastChannelDescriptor[],
  selectedChannels: string[]
): number {
  const limits = channels
    .filter(
      (channel) =>
        selectedChannels.includes(channel.name) &&
        channel.maxTextLength > 0 &&
        !channel.supportsPushPreview
    )
    .map((channel) => channel.maxTextLength);
  return limits.length ? Math.min(...limits) : 0;
}

export function selectedPushPreviewChannel(
  channels: BroadcastChannelDescriptor[],
  selectedChannels: string[]
): BroadcastChannelDescriptor | null {
  return (
    channels.find(
      (channel) => selectedChannels.includes(channel.name) && channel.supportsPushPreview
    ) ?? null
  );
}

export function channelNamesForPayload(snapshot: {
  broadcastChannels: BroadcastChannelDescriptor[];
  selectedChannels: string[];
}): string[] {
  const byName = new Map(snapshot.broadcastChannels.map((channel) => [channel.name, channel]));
  return snapshot.selectedChannels
    .filter((name) => {
      const descriptor = byName.get(name);
      return Boolean(descriptor?.available);
    })
    .sort((left, right) => {
      const leftOrder = byName.get(left)?.order ?? LEGACY_CHANNEL_ORDER[left] ?? 1000;
      const rightOrder = byName.get(right)?.order ?? LEGACY_CHANNEL_ORDER[right] ?? 1000;
      return leftOrder - rightOrder || left.localeCompare(right);
    });
}

export function broadcastChannelReason(
  reason: string,
  at: (key: string, params?: Record<string, unknown>, fallback?: string) => string
): string {
  const reasons: Record<string, [string, string]> = {
    queue_unavailable: [
      "broadcast_channel_reason_queue_unavailable",
      "Telegram queue is unavailable",
    ],
    telegram_disabled: ["broadcast_channel_reason_telegram_disabled", "Telegram is disabled"],
    email_not_configured: [
      "broadcast_channel_reason_email_not_configured",
      "Email is not configured",
    ],
    push_disabled: ["broadcast_channel_reason_push_disabled", "Web Push is disabled"],
    push_not_configured: [
      "broadcast_channel_reason_push_not_configured",
      "Web Push is not configured",
    ],
    user_notification_push_disabled: [
      "broadcast_channel_reason_user_notification_push_disabled",
      "Web Push is disabled for user notifications",
    ],
    provider_absent: [
      "broadcast_channel_reason_provider_absent",
      "Delivery provider is not installed",
    ],
    provider_not_configured: [
      "broadcast_channel_reason_provider_not_configured",
      "Delivery provider is not configured",
    ],
    provider_error: ["broadcast_channel_reason_provider_error", "Delivery provider failed"],
  };
  const [key, fallback] = reasons[reason] || ["", reason];
  return key ? at(key, {}, fallback) : fallback;
}
