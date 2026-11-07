export type BroadcastHistoryButton = {
  kind: string;
  label: string;
  url: string;
  promoCode: string;
  section: string;
  platform?: string;
  app?: string;
  labels: Record<string, string>;
  iconCustomEmojiId: string | null;
  iconEmoji: string;
};

export type BroadcastChannelStats = {
  users?: number;
  deliveries?: number;
  queued?: number;
  sent?: number;
  failed?: number;
  skipped?: number;
  reason?: string;
};

export type BroadcastChannelStatRow = {
  name: string;
  label: string;
  users: number | null;
  deliveries: number | null;
  queued: number;
  sent: number;
  failed: number;
  skipped: number;
  reason: string;
};

export type BroadcastHistoryItem = {
  broadcastId: number;
  status: string;
  target: string;
  channels: string[];
  excludeBlockedTelegram: boolean;
  texts: Record<string, string>;
  emailSubjects: Record<string, string>;
  buttons: BroadcastHistoryButton[];
  imageId: string | null;
  scheduledAt: string;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  updatedAt: string;
  recipientCount: number;
  totalDeliveries: number;
  successfulDeliveries: number;
  failedDeliveries: number;
  telegramSent: number;
  telegramFailed: number;
  emailSent: number;
  emailFailed: number;
  channelStats?: Record<string, BroadcastChannelStats>;
  lastError: string | null;
};

type TranslateFn = (key: string, params?: Record<string, unknown>, fallback?: string) => string;

const CHANNEL_LABELS: Record<string, [string, string]> = {
  telegram: ["broadcast_channel_telegram", "Telegram"],
  email: ["broadcast_channel_email", "Email"],
  push: ["broadcast_channel_push", "Web Push"],
};

const CHANNEL_REASONS: Record<string, [string, string]> = {
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

export function channelLabel(name: string, at: TranslateFn): string {
  const [key, fallback] = CHANNEL_LABELS[name] || ["", name];
  return key ? at(key, {}, fallback) : fallback;
}

export function channelReasonLabel(reason: string, at: TranslateFn): string {
  const [key, fallback] = CHANNEL_REASONS[reason] || ["", reason];
  return key ? at(key, {}, fallback) : fallback;
}

function asChannelStats(value: unknown): Record<string, BroadcastChannelStats> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).flatMap(([channel, raw]) => {
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
      const stats = raw as Record<string, unknown>;
      const number = (key: string): number | undefined => {
        const parsed = Number(stats[key]);
        return Number.isFinite(parsed) ? parsed : undefined;
      };
      return [
        [
          channel,
          {
            users: number("users"),
            deliveries: number("deliveries"),
            queued: number("queued"),
            sent: number("sent"),
            failed: number("failed"),
            skipped: number("skipped"),
            reason: String(stats.reason || ""),
          },
        ],
      ];
    })
  );
}

export function channelStatsRows(
  item: BroadcastHistoryItem,
  at: TranslateFn
): BroadcastChannelStatRow[] {
  const entries = Object.entries(item.channelStats || {});
  if (entries.length) {
    return entries.map(([name, stats]) => ({
      name,
      label: channelLabel(name, at),
      users: stats.users ?? null,
      deliveries: stats.deliveries ?? null,
      queued: stats.queued ?? 0,
      sent: stats.sent ?? 0,
      failed: stats.failed ?? 0,
      skipped: stats.skipped ?? 0,
      reason: stats.reason ?? "",
    }));
  }
  // Historical responses predate channel_stats: keep their legacy counters.
  return item.channels.map((name) => ({
    name,
    label: channelLabel(name, at),
    users: null,
    deliveries: null,
    queued: 0,
    sent: name === "telegram" ? item.telegramSent : name === "email" ? item.emailSent : 0,
    failed: name === "telegram" ? item.telegramFailed : name === "email" ? item.emailFailed : 0,
    skipped: 0,
    reason: "",
  }));
}

export function historyItemFromWire(value: unknown): BroadcastHistoryItem | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const broadcastId = Number(item.broadcast_id);
  if (!Number.isFinite(broadcastId) || broadcastId <= 0) return null;
  const record = (input: unknown): Record<string, string> => {
    if (!input || typeof input !== "object" || Array.isArray(input)) return {};
    return Object.fromEntries(
      Object.entries(input as Record<string, unknown>).map(([key, text]) => [
        key,
        String(text || ""),
      ])
    );
  };
  const buttons = Array.isArray(item.buttons)
    ? item.buttons.map((raw) => {
        const button = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
        return {
          kind: String(button.kind || "url"),
          label: String(button.label || ""),
          url: String(button.url || ""),
          promoCode: String(button.promo_code || ""),
          section: String(button.section || ""),
          ...(typeof button.platform === "string" ? { platform: button.platform } : {}),
          ...(typeof button.app === "string" ? { app: button.app } : {}),
          labels: record(button.labels),
          iconCustomEmojiId:
            typeof button.icon_custom_emoji_id === "string" ? button.icon_custom_emoji_id : null,
          iconEmoji: String(button.icon_emoji || ""),
        };
      })
    : [];
  const number = (key: string): number => {
    const parsed = Number(item[key]);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const nullableText = (key: string): string | null =>
    item[key] == null || item[key] === "" ? null : String(item[key]);
  return {
    broadcastId,
    status: String(item.status || "queued"),
    target: String(item.target || "all"),
    channels: Array.isArray(item.channels) ? item.channels.map(String) : [],
    excludeBlockedTelegram: Boolean(item.exclude_blocked_telegram),
    texts: record(item.texts),
    emailSubjects: record(item.email_subjects),
    buttons,
    imageId: nullableText("image_id"),
    scheduledAt: String(item.scheduled_at || ""),
    createdAt: String(item.created_at || ""),
    startedAt: nullableText("started_at"),
    finishedAt: nullableText("finished_at"),
    updatedAt: String(item.updated_at || ""),
    recipientCount: number("recipient_count"),
    totalDeliveries: number("total_deliveries"),
    successfulDeliveries: number("successful_deliveries"),
    failedDeliveries: number("failed_deliveries"),
    telegramSent: number("telegram_sent"),
    telegramFailed: number("telegram_failed"),
    emailSent: number("email_sent"),
    emailFailed: number("email_failed"),
    channelStats: asChannelStats(item.channel_stats),
    lastError: nullableText("last_error"),
  };
}
