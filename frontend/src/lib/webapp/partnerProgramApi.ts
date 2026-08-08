import type {
  ApiClient,
  PartnerClientsResponse,
  PartnerCommissionsResponse,
  PartnerOverviewResponse,
  PartnerWithdrawalsResponse,
} from "./publicApi.js";
import type {
  PartnerClientPreview,
  PartnerCommissionPreview,
  PartnerCurrency,
  PartnerProgramPreview,
  PartnerWithdrawalPreview,
} from "./previewMock/partnerProgram.js";

type Api = ApiClient["api"];

function major(minor: number, scale: number): number {
  return Number(minor || 0) / 10 ** Math.max(0, Number(scale || 0));
}

function queryPath(path: string, currency: string): `${string}?${string}` {
  return `${path}?currency=${encodeURIComponent(currency)}&limit=200`;
}

function sourceLabel(source: string): PartnerClientPreview["source"] {
  if (source === "partner_telegram_link") return "telegram";
  if (source === "partner_web_link") return "web";
  return "import";
}

function commissionStatus(status: string): PartnerCommissionPreview["status"] {
  if (status === "pending" || status === "available" || status === "reversed") return status;
  return "excluded";
}

function withdrawalStatus(status: string): PartnerWithdrawalPreview["status"] {
  if (
    status === "requested" ||
    status === "processing" ||
    status === "paid" ||
    status === "rejected" ||
    status === "failed" ||
    status === "canceled"
  ) {
    return status;
  }
  return "failed";
}

function fieldId(method: PartnerOverviewResponse["withdrawal_methods"][number]): string {
  const first = method.fields[0];
  const configured = first && typeof first.id === "string" ? first.id : "";
  if (configured) return configured;
  if (method.type === "crypto") return "address";
  if (method.type === "sbp") return "phone";
  return "card_number";
}

async function loadCurrencyActivity(
  api: Api,
  currency: PartnerCurrency
): Promise<{
  clients: PartnerClientPreview[];
  commissions: PartnerCommissionPreview[];
  withdrawals: PartnerWithdrawalPreview[];
}> {
  const [clientResponse, commissionResponse, withdrawalResponse] = await Promise.all([
    api(queryPath("/partner/clients", currency) as `/partner/clients?${string}`),
    api(queryPath("/partner/commissions", currency) as `/partner/commissions?${string}`),
    api(queryPath("/partner/withdrawals", currency) as `/partner/withdrawals?${string}`),
  ]);
  const clientsPayload = clientResponse as PartnerClientsResponse;
  const commissionsPayload = commissionResponse as PartnerCommissionsResponse;
  const withdrawalsPayload = withdrawalResponse as PartnerWithdrawalsResponse;
  const clients = clientsPayload.clients.map((item) => ({
    id: item.public_client_id,
    label: item.label,
    attributedAt: item.attributed_at,
    source: sourceLabel(item.source),
    payments: item.payments_count,
    gross: major(item.gross_minor, item.currency_scale),
    currency,
  }));
  const commissions = commissionsPayload.commissions.map((item) => ({
    id: `COM-${item.commission_id}`,
    clientLabel: item.client_label,
    createdAt: item.source_paid_at,
    gross: major(item.gross_amount_minor, item.currency_scale),
    rate: item.commission_bps / 100,
    amount: major(item.commission_amount_minor, item.currency_scale),
    currency: item.currency,
    status: commissionStatus(item.status),
  }));
  const withdrawals = withdrawalsPayload.withdrawals.map((item) => ({
    id: String(item.withdrawal_id),
    createdAt: item.requested_at,
    method: item.method_type as PartnerWithdrawalPreview["method"],
    masked: item.masked_requisites,
    amount: major(item.amount_minor, item.currency_scale),
    currency: item.currency,
    status: withdrawalStatus(item.status),
    message: item.status_message || undefined,
  }));
  return { clients, commissions, withdrawals };
}

export function partnerPreviewMode(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).has("partner_scenario");
}

export async function loadPartnerProgram(api: Api): Promise<PartnerProgramPreview> {
  const overview = (await api("/partner/overview")) as PartnerOverviewResponse;
  const profile = overview.profile;
  const application = overview.application;
  const currencies = overview.balances.map((item) => item.currency);
  const activity = profile
    ? await Promise.all(currencies.map((currency) => loadCurrencyActivity(api, currency)))
    : [];
  return {
    applicationMaxLength: overview.application_message_max_length,
    applicationState:
      application?.status === "pending"
        ? "pending"
        : application?.status === "rejected"
          ? "rejected"
          : "none",
    applicationMessage: application?.message || "",
    applicationSubmittedAt: application?.submitted_at || "",
    applicationDecisionAt: application?.decided_at || "",
    decisionMessage: application?.decision_message || "",
    reapplyAllowed: Boolean(
      application?.reapply_allowed_at &&
      new Date(application.reapply_allowed_at).getTime() <= Date.now()
    ),
    profileState: profile ? (profile.status as PartnerProgramPreview["profileState"]) : null,
    pauseReason: profile?.pause_reason || "",
    welcomeMessage: profile?.welcome_message || "",
    commissionBps: profile?.commission_bps || 0,
    balances: overview.balances.map((item) => ({
      currency: item.currency,
      available: major(item.available_minor, item.currency_scale),
      pending: major(item.pending_minor, item.currency_scale),
      reserved: major(item.reserved_minor, item.currency_scale),
      lifetime: major(item.lifetime_earned_minor, item.currency_scale),
      scale: item.currency_scale,
    })),
    links: overview.links
      ? [
          ...(overview.links.telegram
            ? [
                {
                  id: "telegram" as const,
                  url: overview.links.telegram,
                  enabled: overview.links.telegram_enabled,
                },
              ]
            : []),
          ...(overview.links.web
            ? [
                {
                  id: "web" as const,
                  url: overview.links.web,
                  enabled: overview.links.web_enabled,
                },
              ]
            : []),
        ]
      : [],
    methods:
      overview.withdrawals_enabled && overview.encryption_available
        ? overview.withdrawal_methods.map((method) => ({
            id: method.id,
            type: method.type as "bank_card" | "sbp" | "crypto",
            currency: method.debit_currency,
            minimum: major(method.min_amount_minor, method.currency_scale),
            enabled: method.enabled,
            networks: method.networks.map((network) => network.id),
            fieldId: fieldId(method),
            scale: method.currency_scale,
          }))
        : [],
    clients: activity.flatMap((item) => item.clients),
    commissions: activity.flatMap((item) => item.commissions),
    withdrawals: activity.flatMap((item) => item.withdrawals),
    loading: false,
    error: false,
    validationError: false,
    tutorialStep: 0,
  };
}
