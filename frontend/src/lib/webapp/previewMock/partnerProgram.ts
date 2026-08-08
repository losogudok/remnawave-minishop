export type PartnerApplicationState = "none" | "pending" | "rejected";
export type PartnerProfileState = "active" | "paused" | "closed";
export type PartnerCurrency = string;

export type PartnerBalancePreview = {
  currency: PartnerCurrency;
  available: number;
  pending: number;
  reserved: number;
  lifetime: number;
  scale?: number;
};

export type PartnerLinkPreview = {
  id: "telegram" | "web";
  url: string;
  enabled: boolean;
};

export type PartnerWithdrawalMethodPreview = {
  id: string;
  type: "bank_card" | "sbp" | "crypto";
  currency: PartnerCurrency;
  minimum: number;
  enabled: boolean;
  networks?: string[];
  fieldId?: string;
  scale?: number;
};

export type PartnerClientPreview = {
  id: string;
  label: string;
  attributedAt: string;
  source: "telegram" | "web" | "import";
  payments: number;
  gross: number;
  currency: PartnerCurrency;
};

export type PartnerCommissionPreview = {
  id: string;
  clientLabel: string;
  createdAt: string;
  gross: number;
  rate: number;
  amount: number;
  currency: PartnerCurrency;
  status: "available" | "pending" | "reversed" | "excluded";
};

export type PartnerWithdrawalPreview = {
  id: string;
  createdAt: string;
  method: "bank_card" | "sbp" | "crypto";
  masked: string;
  amount: number;
  currency: PartnerCurrency;
  status: "requested" | "processing" | "paid" | "rejected" | "failed" | "canceled";
  message?: string;
};

export type PartnerProgramPreview = {
  applicationMaxLength: number;
  applicationState: PartnerApplicationState;
  applicationMessage: string;
  applicationSubmittedAt: string;
  applicationDecisionAt: string;
  decisionMessage: string;
  reapplyAllowed: boolean;
  profileState: PartnerProfileState | null;
  pauseReason: string;
  welcomeMessage: string;
  commissionBps: number;
  balances: PartnerBalancePreview[];
  links: PartnerLinkPreview[];
  methods: PartnerWithdrawalMethodPreview[];
  clients: PartnerClientPreview[];
  commissions: PartnerCommissionPreview[];
  withdrawals: PartnerWithdrawalPreview[];
  loading: boolean;
  error: boolean;
  validationError: boolean;
  tutorialStep: number;
};

function previewSearch(): URLSearchParams {
  if (typeof window === "undefined") return new URLSearchParams();
  return new URLSearchParams(window.location.search);
}

export function partnerProgramPreview(t: Translate = (key) => key): PartnerProgramPreview {
  const query = previewSearch();
  const scenario = String(query.get("partner_scenario") || "no_application").toLowerCase();
  const active = scenario.startsWith("active") || scenario === "paused";
  const empty = scenario === "active_empty";
  const multiCurrency = scenario === "active_multi_currency";
  const belowMinimum = scenario === "active_below_minimum";
  const pending = scenario === "pending";
  const rejected =
    scenario === "rejected" || scenario === "rejected_no_message" || scenario === "reapply";
  const populated = active && !empty;

  const balances: PartnerBalancePreview[] = active
    ? [
        {
          currency: "RUB",
          available: scenario === "active_negative" ? -237 : belowMinimum ? 120 : 12840,
          pending: 1480,
          reserved: 3500,
          lifetime: 48270,
        },
        ...(multiCurrency
          ? [
              {
                currency: "USD" as const,
                available: 184.5,
                pending: 22.4,
                reserved: 0,
                lifetime: 620.75,
              },
            ]
          : []),
      ]
    : [];

  const demoRowIndexes = populated ? Array.from({ length: 17 }, (_, index) => index + 1) : [];
  const additionalClients: PartnerClientPreview[] = demoRowIndexes.map((index) => ({
    id: `C-D${String(index).padStart(2, "0")}`,
    label: `Demo client ${String(index).padStart(2, "0")}`,
    attributedAt: `2026-06-${String(30 - index).padStart(2, "0")}T10:20:00Z`,
    source: (["telegram", "web", "import"] as const)[index % 3],
    payments: 1 + (index % 4),
    gross: 1200 - index * 40,
    currency: "RUB",
  }));
  const additionalCommissions: PartnerCommissionPreview[] = demoRowIndexes.map((index) => {
    const gross = 1200 - index * 40;
    const status = (["available", "pending", "reversed"] as const)[index % 3];
    return {
      id: `COM-D${String(index).padStart(2, "0")}`,
      clientLabel: `Demo client ${String(index).padStart(2, "0")}`,
      createdAt: `2026-06-${String(30 - index).padStart(2, "0")}T14:03:00Z`,
      gross,
      rate: 30,
      amount: status === "reversed" ? -(gross * 0.3) : gross * 0.3,
      currency: "RUB",
      status,
    };
  });
  const additionalWithdrawals: PartnerWithdrawalPreview[] = demoRowIndexes.map((index) => {
    const method = (["bank_card", "sbp", "crypto"] as const)[index % 3];
    return {
      id: `WD-D${String(index).padStart(2, "0")}`,
      createdAt: `2026-06-${String(30 - index).padStart(2, "0")}T12:05:00Z`,
      method,
      masked:
        method === "crypto"
          ? `TRC20 ••••D${String(index).padStart(2, "0")}`
          : method === "sbp"
            ? `+7 ••• •••-${String(10 + index).padStart(2, "0")}`
            : `•••• ${2000 + index}`,
      amount: 1000 + index * 250,
      currency: "RUB",
      status: (["paid", "processing", "rejected", "requested"] as const)[index % 4],
    };
  });

  return {
    applicationMaxLength: 2000,
    applicationState: pending ? "pending" : rejected ? "rejected" : "none",
    applicationMessage:
      scenario === "validation_error"
        ? t("wa_partner_preview_short_application")
        : t("wa_partner_preview_application_message"),
    applicationSubmittedAt: "2026-08-04T12:20:00Z",
    applicationDecisionAt: "2026-08-06T09:15:00Z",
    decisionMessage:
      rejected && scenario !== "rejected_no_message"
        ? t("wa_partner_preview_decision_message")
        : "",
    reapplyAllowed: scenario === "reapply",
    profileState: active ? (scenario === "paused" ? "paused" : "active") : null,
    pauseReason: scenario === "paused" ? t("wa_partner_preview_pause_reason") : "",
    welcomeMessage: t("wa_partner_preview_welcome_message"),
    commissionBps: 3000,
    balances,
    links: active
      ? [
          {
            id: "telegram",
            url: "https://t.me/minishop_bot?start=p_Q7m2pK8v4",
            enabled: true,
          },
          {
            id: "web",
            url: "https://example.com/?start=p_Q7m2pK8v4",
            enabled: true,
          },
        ]
      : [],
    methods: active
      ? [
          { id: "card-rub", type: "bank_card", currency: "RUB", minimum: 500, enabled: true },
          { id: "sbp-rub", type: "sbp", currency: "RUB", minimum: 300, enabled: true },
          {
            id: "usdt-rub",
            type: "crypto",
            currency: "RUB",
            minimum: 3000,
            enabled: true,
            networks: ["TRC20", "TON"],
          },
        ]
      : [],
    clients: populated
      ? [
          {
            id: "C-7K2A",
            label: "Alex M.",
            attributedAt: "2026-07-28T10:20:00Z",
            source: "telegram",
            payments: 3,
            gross: 4770,
            currency: "RUB",
          },
          {
            id: "C-9P4D",
            label: "Customer 9P4D",
            attributedAt: "2026-07-31T16:05:00Z",
            source: "web",
            payments: 2,
            gross: 3180,
            currency: "RUB",
          },
          {
            id: "C-2N8F",
            label: "Mila K.",
            attributedAt: "2026-08-02T08:40:00Z",
            source: "import",
            payments: 1,
            gross: 1590,
            currency: "RUB",
          },
          {
            id: "C-4R1X",
            label: "Noor A.",
            attributedAt: "2026-07-29T12:15:00Z",
            source: "telegram",
            payments: 1,
            gross: 1200,
            currency: "RUB",
          },
          ...additionalClients,
        ]
      : [],
    commissions: populated
      ? [
          {
            id: "COM-184",
            clientLabel: "Alex M.",
            createdAt: "2026-08-06T11:12:00Z",
            gross: 1590,
            rate: 30,
            amount: 477,
            currency: "RUB",
            status: "available",
          },
          {
            id: "COM-179",
            clientLabel: "Customer 9P4D",
            createdAt: "2026-08-04T18:34:00Z",
            gross: 1590,
            rate: 30,
            amount: 477,
            currency: "RUB",
            status: "pending",
          },
          {
            id: "COM-166",
            clientLabel: "Mila K.",
            createdAt: "2026-08-01T14:03:00Z",
            gross: 790,
            rate: 30,
            amount: -237,
            currency: "RUB",
            status: "reversed",
          },
          {
            id: "COM-152",
            clientLabel: "Noor A.",
            createdAt: "2026-07-29T17:20:00Z",
            gross: 1200,
            rate: 30,
            amount: 360,
            currency: "RUB",
            status: "available",
          },
          ...additionalCommissions,
        ]
      : [],
    withdrawals: populated
      ? [
          {
            id: "WD-42",
            createdAt: "2026-08-06T13:30:00Z",
            method: "bank_card",
            masked: "•••• 4242",
            amount: 3500,
            currency: "RUB",
            status: "requested",
          },
          {
            id: "WD-31",
            createdAt: "2026-07-22T09:10:00Z",
            method: "sbp",
            masked: "+7 ••• •••-12-34",
            amount: 6200,
            currency: "RUB",
            status: "paid",
          },
          {
            id: "WD-28",
            createdAt: "2026-07-18T17:45:00Z",
            method: "crypto",
            masked: "TRC20 ••••8Fx2",
            amount: 8200,
            currency: "RUB",
            status: "processing",
          },
          {
            id: "WD-19",
            createdAt: "2026-07-02T12:05:00Z",
            method: "bank_card",
            masked: "•••• 1024",
            amount: 2100,
            currency: "RUB",
            status: "rejected",
            message: t("wa_partner_preview_withdrawal_rejected"),
          },
          ...additionalWithdrawals,
        ]
      : [],
    loading: scenario === "loading",
    error: scenario === "error" || scenario === "offline",
    validationError: scenario === "validation_error",
    tutorialStep: Math.max(0, Math.min(4, Number(query.get("partner_tutorial") || 0))),
  };
}

export type PartnerBalanceCheckoutPreview = {
  available: string;
  due: string;
  shortage: string;
  enabled: boolean;
  reasonKey: string;
};

export function partnerBalanceCheckoutPreview(): PartnerBalanceCheckoutPreview | null {
  const scenario = String(previewSearch().get("partner_checkout") || "").toLowerCase();
  if (!scenario) return null;
  if (scenario === "enabled") {
    return { available: "2,840 RUB", due: "190 RUB", shortage: "", enabled: true, reasonKey: "" };
  }
  if (scenario === "negative") {
    return {
      available: "-237 RUB",
      due: "190 RUB",
      shortage: "427 RUB",
      enabled: false,
      reasonKey: "wa_partner_balance_negative",
    };
  }
  return {
    available: "120 RUB",
    due: "190 RUB",
    shortage: "70 RUB",
    enabled: false,
    reasonKey: "wa_partner_balance_insufficient",
  };
}
import type { Translate } from "$lib/webapp/types.js";
