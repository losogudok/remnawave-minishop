import type { SubscriptionView } from "./types.js";

export function parseSubscriptionEndMs(sub: SubscriptionView): number | null {
  const raw = String(sub?.end_date || "").trim();
  if (!raw) return null;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

export function dateOnlyFromEndText(text: unknown): string {
  const value = String(text || "").trim();
  if (!value) return "";
  return value.split(/\s+/)[0] || value;
}

export function dateOnlyFromIso(text: unknown): string {
  const match = String(text || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[3]}.${match[2]}.${match[1]}` : "";
}

export function subscriptionEndDateLabel(sub: SubscriptionView): string {
  return dateOnlyFromEndText(sub?.end_date_text) || dateOnlyFromIso(sub?.end_date);
}

export function formatSubscriptionCountdown(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}
