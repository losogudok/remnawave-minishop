import type { components } from "../api/openapi.generated";

export type AdminAdStats = components["schemas"]["AdStatsOut"];

function adStat(stats: AdminAdStats | null | undefined, key: keyof AdminAdStats): number {
  const value = Number(stats?.[key]);
  return Number.isFinite(value) ? value : 0;
}

export function adRegistrationCount(stats: AdminAdStats | null | undefined): number {
  return adStat(stats, "starts");
}

export function adConversionCount(stats: AdminAdStats | null | undefined): number {
  return adStat(stats, "payers");
}
