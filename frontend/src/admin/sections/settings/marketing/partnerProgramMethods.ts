export type MethodType = "bank_card" | "sbp" | "crypto";
export type CryptoNetwork = { id: string; label: string };
export type WithdrawalMethod = {
  id: string;
  type: MethodType;
  enabled: boolean;
  label: string;
  currency: string;
  scale: number;
  minimum: number;
  maximum: number | null;
  networks: CryptoNetwork[];
};

export function partnerSettingsScenario(): string {
  if (typeof window === "undefined") return "";
  return String(
    new URLSearchParams(window.location.search).get("partner_settings_scenario") || ""
  ).toLowerCase();
}

export function normalizeWithdrawalMethods(values: unknown[]): WithdrawalMethod[] {
  return values.map((value) => {
    const item = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
    const scale = Number(item.currency_scale || 2);
    return {
      id: String(item.id || ""),
      type: String(item.type || "bank_card") as MethodType,
      enabled: item.enabled !== false,
      label: String(item.label || item.settlement_asset || ""),
      currency: String(item.debit_currency || "RUB"),
      scale,
      minimum: Number(item.min_amount_minor || 0) / 10 ** scale,
      maximum:
        item.max_amount_minor == null ? null : Number(item.max_amount_minor || 0) / 10 ** scale,
      networks: Array.isArray(item.networks)
        ? item.networks.map((network) => {
            const entry = network as Record<string, unknown>;
            return { id: String(entry.id || ""), label: String(entry.label || "") };
          })
        : [],
    };
  });
}

export function previewWithdrawalMethods(scenario: string): WithdrawalMethod[] {
  return [
    {
      id: "card-rub",
      type: "bank_card",
      enabled: scenario !== "disabled_method",
      label: "",
      currency: "RUB",
      scale: 2,
      minimum: 500,
      maximum: 100000,
      networks: [],
    },
    {
      id: "sbp-rub",
      type: "sbp",
      enabled: true,
      label: "",
      currency: "RUB",
      scale: 2,
      minimum: 300,
      maximum: 150000,
      networks: [],
    },
    {
      id: "usdt-rub",
      type: "crypto",
      enabled: true,
      label: "USDT",
      currency: "RUB",
      scale: 2,
      minimum: 3000,
      maximum: null,
      networks:
        scenario === "crypto_warning"
          ? []
          : [
              { id: "tron", label: "TRC20" },
              { id: "ton", label: "TON" },
            ],
    },
  ];
}
