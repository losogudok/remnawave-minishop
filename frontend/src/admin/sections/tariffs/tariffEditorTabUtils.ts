import { normalizeCurrencyKey } from "$lib/admin/tariffDraft";
import type {
  DraftRowsField,
  DraftSquadField,
  PanelSquad,
  TariffsCatalog,
  TariffsStore,
} from "$lib/admin/stores/tariffsStore";

export type TranslateFn = (
  key: string,
  params?: Record<string, unknown>,
  fallback?: string
) => string;
export type SelectOption = { value: string; label: string };
export type DraftRow = Record<string, string | number | undefined>;
export type ReorderHandler = (from: number, to: number) => void;

export function draftRowKey(_row: DraftRow, index: number): number {
  return index;
}

export function inputValue(event: Event): string {
  return (event.currentTarget as HTMLInputElement | null)?.value ?? "";
}

export function draftInputHandler(
  tariffsStore: TariffsStore,
  field: string
): (event: Event) => void {
  return (event) => tariffsStore.updateDraftField(field, inputValue(event));
}

export function draftRowInputHandler(
  tariffsStore: TariffsStore,
  field: DraftRowsField,
  index: number,
  key: string
): (event: Event) => void {
  return (event) => tariffsStore.updateDraftRow(field, index, { [key]: inputValue(event) });
}

export function addDraftSquad(
  tariffsStore: TariffsStore,
  field: DraftSquadField,
  value: string
): void {
  tariffsStore.addSquadToDraft(field, value);
  tariffsStore.updateState({
    selectedBaseSquad: field === "squadUuids" ? "" : tariffsStore.selectedBaseSquad,
    selectedPremiumSquad: field === "premiumSquadUuids" ? "" : tariffsStore.selectedPremiumSquad,
  });
}

export function panelSquadOptions(panelSquads: PanelSquad[]): SelectOption[] {
  return (panelSquads || []).map((squad) => ({
    value: squad.uuid,
    label: squad.name,
  }));
}

export function moveDraftRowHandler(
  tariffsStore: TariffsStore,
  field: DraftRowsField
): ReorderHandler {
  return (from, to) => tariffsStore.moveDraftRow(field, from, to);
}

/**
 * Whether the Tribute provider is switched on for this deployment.
 *
 * The Tribute mapping is a provider integration, not a property of the tariff,
 * so its fields only make sense — and are only ever read — while the provider
 * is enabled. The tariffs endpoint already reports every provider's state, so
 * no extra request is needed.
 */
export function tributeEnabled(tariffsStore: TariffsStore): boolean {
  return (tariffsStore.providerCurrencySupport || []).some(
    (provider) => provider.id === "tribute" && provider.enabled
  );
}

function hasTributeBinding(rows: unknown, keys: readonly string[]): boolean {
  if (!Array.isArray(rows)) return false;
  return rows.some((row) => {
    const record = (row || {}) as Record<string, unknown>;
    return keys.some((key) => String(record[key] ?? "").trim() !== "");
  });
}

/**
 * Whether the Tribute block belongs on screen for this tariff.
 *
 * Hiding it the moment the provider is switched off left whatever the tariff
 * already carried invisible — and the editor still submits it, so a leftover
 * binding could make the catalog unsavable with no field to clear.
 */
export function tributeSectionVisible(tariffsStore: TariffsStore, tariffDraft: unknown): boolean {
  if (tributeEnabled(tariffsStore)) return true;
  const draft = (tariffDraft || {}) as Record<string, unknown>;
  if (String(draft.tributeLink ?? "").trim() || String(draft.tributeSubscriptionId ?? "").trim()) {
    return true;
  }
  const periodKeys = ["tribute_link", "tribute_subscription_id", "tribute_period_id"] as const;
  const productKeys = ["tribute_product_id", "tribute_product_link"] as const;
  return (
    hasTributeBinding(draft.periodRows, periodKeys) ||
    hasTributeBinding(draft.topupRows, productKeys) ||
    hasTributeBinding(draft.premiumTopupRows, productKeys) ||
    hasTributeBinding(draft.trafficRows, productKeys)
  );
}

export function defaultCurrencyCode(tariffsCatalog: TariffsCatalog): string {
  return (normalizeCurrencyKey(tariffsCatalog?.default_currency || "rub") as string).toUpperCase();
}

export function currencyPriceColumnLabel(at: TranslateFn, currency: string): string {
  return at("tariff_col_price_currency", { currency }, "Price, {currency}");
}

export function currencyPriceAriaLabel(at: TranslateFn, currency: string): string {
  return at("tariff_label_price_currency", { currency }, "Price in {currency}");
}

export function conversionCurrencyLabel(at: TranslateFn, currency: string): string {
  return at(
    "tariff_label_conversion_currency",
    { currency },
    "Conversion rate, {currency} per 1 GB"
  );
}
