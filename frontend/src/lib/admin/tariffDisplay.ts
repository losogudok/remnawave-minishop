import { normalizeCurrencyKey } from "./tariffDraft";
import type { Tariff, TariffsCatalog } from "./stores/tariffsStore";

type Translate = (key: string, params?: Record<string, unknown>, fallback?: string) => string;
type MoneyFormatter = (value: unknown, currency?: string) => string;

export function tariffDisplayName(tariff: Tariff): string {
  return tariff?.names?.ru || tariff?.names?.en || tariff?.key || "—";
}

export function tariffDisplaySortKey(tariff: Tariff): string {
  return tariff.key;
}

export function tariffDisplayPriceSummary(
  tariff: Tariff,
  catalog: TariffsCatalog,
  translate: Translate,
  formatMoney: MoneyFormatter
): string {
  const currency = normalizeCurrencyKey(catalog.default_currency || "rub");
  const currencyCode = currency.toUpperCase();
  if (tariff.billing_model === "traffic") {
    const first = (tariff.traffic_packages?.[currency] || [])[0];
    return first
      ? `${first.gb} GB ${translate("at", {}, "for")} ${formatMoney(first.price, currencyCode)}`
      : translate("tariff_traffic_packages", {}, "Traffic packages");
  }
  return [...(tariff.enabled_periods || [])]
    .map((month) => {
      const price =
        (currency === "rub" ? tariff.prices_rub?.[String(month)] : undefined) ??
        tariff.prices?.[currency]?.[String(month)];
      const stars = tariff.prices_stars?.[String(month)];
      if (price)
        return `${month} ${translate("months_short", {}, "mo.")} ${formatMoney(price, currencyCode)}`;
      if (stars) return `${month} ${translate("months_short", {}, "mo.")} ${stars} ⭐`;
      return `${month} ${translate("months_short", {}, "mo.")}`;
    })
    .join(" · ");
}

export function tariffGbLimit(value: unknown, unlimitedLabel: string): string {
  const gb = Number(value || 0);
  return !Number.isFinite(gb) || gb <= 0 ? unlimitedLabel : `${gb} GB`;
}

export function tariffPremiumLimit(tariff: Tariff, unlimitedLabel: string): string {
  if (!(tariff.premium_squad_uuids || []).length) return "—";
  if (tariff.premium_unlimited) return unlimitedLabel;
  const gb = Number(tariff.premium_monthly_gb ?? 0);
  return `${Number.isFinite(gb) && gb >= 0 ? gb : 0} GB`;
}

export function tariffHwidLimit(tariff: Tariff, unlimitedLabel: string): string {
  const rawLimit = tariff.hwid_device_limit;
  if (rawLimit === null || rawLimit === undefined) return "env";
  return Number.isFinite(Number(rawLimit)) && Number(rawLimit) === 0
    ? unlimitedLabel
    : String(rawLimit);
}
