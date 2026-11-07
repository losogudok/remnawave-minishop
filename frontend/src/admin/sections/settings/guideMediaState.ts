import type { components } from "$lib/api/openapi.generated.js";

export type GuideMediaCatalogConfig = components["schemas"]["GuideMediaCatalogConfigOut"];

export type CatalogState = "unavailable" | "empty" | "ready";
export type OrphanState = "orphaned" | "known" | "unknown";

export type CatalogProblem = {
  config_id: string;
  source: string;
  error: string | null;
  missing: boolean;
};

export function catalogState(
  catalog: readonly GuideMediaCatalogConfig[],
  available: boolean
): CatalogState {
  if (!available) return "unavailable";
  if (catalog.length === 0) return "empty";
  const hasPlatform = catalog.some((config) => (config.platforms ?? []).length > 0);
  const hasProblem = catalog.some((config) => config.missing || Boolean(config.error));
  return hasPlatform || hasProblem ? "ready" : "empty";
}

export function catalogProblems(catalog: readonly GuideMediaCatalogConfig[]): CatalogProblem[] {
  return catalog
    .filter((config) => config.missing || Boolean(config.error))
    .map((config) => ({
      config_id: config.config_id,
      source: config.source,
      error: config.error ?? null,
      missing: Boolean(config.missing),
    }));
}

export function orphanState(orphaned: boolean, known: boolean): OrphanState {
  if (!known) return "unknown";
  return orphaned ? "orphaned" : "known";
}
