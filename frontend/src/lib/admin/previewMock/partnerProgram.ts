/**
 * Preview data for the partner-program prototype.
 *
 * Every person here is an existing user from the shared demo dataset
 * (`demoDataset.js`), referenced by their real `user_id`, and every commission
 * points at a real payment id. That keeps "open user card" / "open payment"
 * working against the same mock API the rest of the admin panel uses, instead
 * of inventing a second cast of people.
 *
 * Names and handles are read back out of the dataset rather than copied, so a
 * partner row can never disagree with the user card it links to.
 */

import { DATASET } from "../../webapp/mockApi/dataset.js";

const demoUsers = new Map(
  (DATASET.adminUsers ?? []).map((user) => [Number(user.user_id ?? user.id ?? 0), user])
);

function nameOf(userId: number): string {
  const user = demoUsers.get(userId);
  if (!user) return `#${userId}`;
  const full = [user.first_name, user.last_name].filter(Boolean).join(" ").trim();
  return full || user.username || `#${userId}`;
}

function handleOf(userId: number): string {
  const username = demoUsers.get(userId)?.username;
  return username ? `@${username}` : `#${userId}`;
}

export type PartnerStatus = "active" | "paused" | "closed";
export type ApplicationStatus = "pending" | "approved" | "rejected" | "canceled";
export type WithdrawalStatus =
  "requested" | "processing" | "paid" | "rejected" | "failed" | "canceled";
export type WithdrawalMethod = "bank_card" | "sbp" | "crypto";

export type PartnerRow = {
  id: string;
  userId: number;
  name: string;
  handle: string;
  status: PartnerStatus;
  rate: number;
  clients: number;
  payments: number;
  gross: number;
  earned: number;
  available: number;
  currencyScale?: number;
  activated: string;
};

export type ApplicationRow = {
  id: string;
  userId: number;
  user: string;
  handle: string;
  submitted: string;
  status: ApplicationStatus;
  messageKey: string;
};

export type WithdrawalRow = {
  id: string;
  partnerId: string;
  partner: string;
  handle: string;
  method: WithdrawalMethod;
  masked: string;
  amount: number;
  status: WithdrawalStatus;
  requested: string;
  processedAt: string;
  noteKey: string;
  statusVersion?: number;
  externalReference?: string;
  settlementAmount?: string;
};

export const partners: PartnerRow[] = [
  {
    id: "PT-104",
    userId: 910211,
    name: nameOf(910211),
    handle: handleOf(910211),
    status: "active",
    rate: 30,
    clients: 24,
    payments: 58,
    gross: 128400,
    earned: 38520,
    available: 12840,
    activated: "2026-04-18 10:24",
  },
  {
    id: "PT-098",
    userId: 910256,
    name: nameOf(910256),
    handle: handleOf(910256),
    status: "active",
    rate: 35,
    clients: 18,
    payments: 43,
    gross: 96700,
    earned: 33845,
    available: 7210,
    activated: "2026-03-29 16:02",
  },
  {
    id: "PT-081",
    userId: 910157,
    name: nameOf(910157),
    handle: handleOf(910157),
    status: "paused",
    rate: 30,
    clients: 10,
    payments: 21,
    gross: 42090,
    earned: 12627,
    available: 0,
    activated: "2026-02-11 09:40",
  },
  {
    id: "PT-077",
    userId: 910207,
    name: nameOf(910207),
    handle: handleOf(910207),
    status: "active",
    rate: 25,
    clients: 7,
    payments: 16,
    gross: 28400,
    earned: 7100,
    available: 3100,
    activated: "2026-01-24 21:15",
  },
  {
    id: "PT-064",
    userId: 910023,
    name: nameOf(910023),
    handle: handleOf(910023),
    status: "active",
    rate: 28,
    clients: 12,
    payments: 27,
    gross: 51300,
    earned: 14364,
    available: 4820,
    activated: "2025-12-08 12:33",
  },
  {
    id: "PT-052",
    userId: 910031,
    name: nameOf(910031),
    handle: handleOf(910031),
    status: "paused",
    rate: 20,
    clients: 4,
    payments: 6,
    gross: 9600,
    earned: 1920,
    available: -240,
    activated: "2025-11-02 18:07",
  },
];

export const applications: ApplicationRow[] = [
  {
    id: "APP-1042",
    userId: 910019,
    user: nameOf(910019),
    handle: handleOf(910019),
    submitted: "2026-08-06 21:23",
    status: "pending",
    messageKey: "partners_preview_application_guides",
  },
  {
    id: "APP-1039",
    userId: 910007,
    user: nameOf(910007),
    handle: handleOf(910007),
    submitted: "2026-08-05 09:40",
    status: "pending",
    messageKey: "partners_preview_application_community",
  },
  {
    id: "APP-1028",
    userId: 910011,
    user: nameOf(910011),
    handle: handleOf(910011),
    submitted: "2026-08-01 12:14",
    status: "approved",
    messageKey: "partners_preview_application_privacy",
  },
  {
    id: "APP-1015",
    userId: 910035,
    user: nameOf(910035),
    handle: handleOf(910035),
    submitted: "2026-07-27 16:52",
    status: "rejected",
    messageKey: "partners_preview_application_spam",
  },
];

export const withdrawals: WithdrawalRow[] = [
  {
    id: "WD-502",
    partnerId: "PT-104",
    partner: nameOf(910211),
    handle: handleOf(910211),
    method: "bank_card",
    masked: "•••• 4242",
    amount: 3500,
    status: "requested",
    requested: "2026-08-07 09:12",
    processedAt: "",
    noteKey: "",
  },
  {
    id: "WD-499",
    partnerId: "PT-098",
    partner: nameOf(910256),
    handle: handleOf(910256),
    method: "crypto",
    masked: "TRC20 ••••8Fx2",
    amount: 8200,
    status: "processing",
    requested: "2026-08-06 14:36",
    processedAt: "2026-08-06 15:02",
    noteKey: "",
  },
  {
    id: "WD-488",
    partnerId: "PT-077",
    partner: nameOf(910207),
    handle: handleOf(910207),
    method: "sbp",
    masked: "+7 ••• •••-12-34",
    amount: 3100,
    status: "paid",
    requested: "2026-08-03 18:05",
    processedAt: "2026-08-04 10:19",
    noteKey: "",
  },
  {
    id: "WD-476",
    partnerId: "PT-064",
    partner: nameOf(910023),
    handle: handleOf(910023),
    method: "bank_card",
    masked: "•••• 1024",
    amount: 2100,
    status: "rejected",
    requested: "2026-07-29 11:41",
    processedAt: "2026-07-29 12:20",
    noteKey: "partners_preview_withdrawal_rejected",
  },
  {
    id: "WD-461",
    partnerId: "PT-104",
    partner: nameOf(910211),
    handle: handleOf(910211),
    method: "sbp",
    masked: "+7 ••• •••-77-05",
    amount: 5400,
    status: "paid",
    requested: "2026-07-21 08:19",
    processedAt: "2026-07-21 19:44",
    noteKey: "",
  },
];

/** Rows behind the partner-detail tabs; each tab shows a different entity. */
export type PartnerClientRow = {
  id: string;
  userId: number;
  label: string;
  handle: string;
  attributed: string;
  source: "telegram" | "web" | "import";
  payments: number;
  gross: number;
};

export type PartnerCommissionRow = {
  id: string;
  paymentId: number;
  clientUserId: number;
  client: string;
  clientHandle: string;
  created: string;
  gross: number;
  rate: number;
  amount: number;
  status: "available" | "pending" | "reversed" | "excluded";
};

export type PartnerLedgerRow = {
  id: string;
  created: string;
  kindKey: string;
  amount: number;
  balanceAfter: number;
  /** Withdrawal or commission this entry came from, when there is one. */
  refId: string;
  refKind: "withdrawal" | "commission" | "";
};

export type PartnerAuditRow = {
  id: string;
  created: string;
  actorUserId: number;
  actor: string;
  actionKey: string;
  detail: string;
};

export const partnerClients: PartnerClientRow[] = [
  {
    id: "C-7K2A",
    userId: 910001,
    label: nameOf(910001),
    handle: handleOf(910001),
    attributed: "2026-07-28 10:20",
    source: "telegram",
    payments: 3,
    gross: 4770,
  },
  {
    id: "C-9P4D",
    userId: 910003,
    label: nameOf(910003),
    handle: handleOf(910003),
    attributed: "2026-07-31 16:05",
    source: "web",
    payments: 2,
    gross: 3180,
  },
  {
    id: "C-2N8F",
    userId: 910015,
    label: nameOf(910015),
    handle: handleOf(910015),
    attributed: "2026-08-02 08:40",
    source: "import",
    payments: 1,
    gross: 1590,
  },
  {
    id: "C-4T1B",
    userId: 910027,
    label: nameOf(910027),
    handle: handleOf(910027),
    attributed: "2026-08-04 19:02",
    source: "telegram",
    payments: 4,
    gross: 6360,
  },
  {
    id: "C-8M3P",
    userId: 910010,
    label: nameOf(910010),
    handle: handleOf(910010),
    attributed: "2026-08-03 11:48",
    source: "telegram",
    payments: 3,
    gross: 4770,
  },
  {
    id: "C-1QX9",
    userId: 910017,
    label: nameOf(910017),
    handle: handleOf(910017),
    attributed: "2026-08-01 09:15",
    source: "web",
    payments: 2,
    gross: 3180,
  },
  {
    id: "C-6R4K",
    userId: 910022,
    label: nameOf(910022),
    handle: handleOf(910022),
    attributed: "2026-07-30 20:31",
    source: "telegram",
    payments: 5,
    gross: 7950,
  },
  {
    id: "C-3W7V",
    userId: 910006,
    label: nameOf(910006),
    handle: handleOf(910006),
    attributed: "2026-07-27 14:02",
    source: "import",
    payments: 1,
    gross: 1590,
  },
  {
    id: "C-9L2D",
    userId: 910013,
    label: nameOf(910013),
    handle: handleOf(910013),
    attributed: "2026-07-24 08:53",
    source: "telegram",
    payments: 4,
    gross: 6360,
  },
  {
    id: "C-5H8T",
    userId: 910004,
    label: nameOf(910004),
    handle: handleOf(910004),
    attributed: "2026-07-21 17:26",
    source: "web",
    payments: 2,
    gross: 3180,
  },
  {
    id: "C-2Z6N",
    userId: 910020,
    label: nameOf(910020),
    handle: handleOf(910020),
    attributed: "2026-07-18 12:41",
    source: "telegram",
    payments: 6,
    gross: 9540,
  },
  {
    id: "C-7B1F",
    userId: 910009,
    label: nameOf(910009),
    handle: handleOf(910009),
    attributed: "2026-07-15 19:07",
    source: "telegram",
    payments: 1,
    gross: 1590,
  },
];

export const partnerCommissions: PartnerCommissionRow[] = [
  {
    id: "COM-184",
    paymentId: 710024,
    clientUserId: 910001,
    client: nameOf(910001),
    clientHandle: handleOf(910001),
    created: "2026-08-06 11:12",
    gross: 1590,
    rate: 30,
    amount: 477,
    status: "available",
  },
  {
    id: "COM-179",
    paymentId: 710030,
    clientUserId: 910001,
    client: nameOf(910001),
    clientHandle: handleOf(910001),
    created: "2026-08-04 18:34",
    gross: 1590,
    rate: 30,
    amount: 477,
    status: "pending",
  },
  {
    id: "COM-166",
    paymentId: 710031,
    clientUserId: 910256,
    client: nameOf(910256),
    clientHandle: handleOf(910256),
    created: "2026-08-01 14:03",
    gross: 790,
    rate: 30,
    amount: -237,
    status: "reversed",
  },
  {
    id: "COM-158",
    paymentId: 710016,
    clientUserId: 910211,
    client: nameOf(910211),
    clientHandle: handleOf(910211),
    created: "2026-07-29 09:47",
    gross: 3180,
    rate: 30,
    amount: 954,
    status: "available",
  },
  {
    id: "COM-151",
    paymentId: 710041,
    clientUserId: 910010,
    client: nameOf(910010),
    clientHandle: handleOf(910010),
    created: "2026-07-26 16:18",
    gross: 1590,
    rate: 30,
    amount: 477,
    status: "available",
  },
  {
    id: "COM-147",
    paymentId: 710040,
    clientUserId: 910017,
    client: nameOf(910017),
    clientHandle: handleOf(910017),
    created: "2026-07-23 10:05",
    gross: 3180,
    rate: 30,
    amount: 954,
    status: "available",
  },
  {
    id: "COM-142",
    paymentId: 710039,
    clientUserId: 910022,
    client: nameOf(910022),
    clientHandle: handleOf(910022),
    created: "2026-07-20 21:44",
    gross: 790,
    rate: 30,
    amount: 237,
    status: "available",
  },
  {
    id: "COM-138",
    paymentId: 710038,
    clientUserId: 910006,
    client: nameOf(910006),
    clientHandle: handleOf(910006),
    created: "2026-07-17 13:29",
    gross: 1590,
    rate: 30,
    amount: 477,
    status: "pending",
  },
  {
    id: "COM-133",
    paymentId: 710037,
    clientUserId: 910013,
    client: nameOf(910013),
    clientHandle: handleOf(910013),
    created: "2026-07-14 07:52",
    gross: 6360,
    rate: 30,
    amount: 1908,
    status: "available",
  },
  {
    id: "COM-129",
    paymentId: 710036,
    clientUserId: 910004,
    client: nameOf(910004),
    clientHandle: handleOf(910004),
    created: "2026-07-11 18:36",
    gross: 1590,
    rate: 30,
    amount: 477,
    status: "available",
  },
  {
    id: "COM-124",
    paymentId: 710035,
    clientUserId: 910020,
    client: nameOf(910020),
    clientHandle: handleOf(910020),
    created: "2026-07-08 11:19",
    gross: 3180,
    rate: 30,
    amount: 954,
    status: "reversed",
  },
  {
    id: "COM-118",
    paymentId: 710034,
    clientUserId: 910009,
    client: nameOf(910009),
    clientHandle: handleOf(910009),
    created: "2026-07-05 15:03",
    gross: 1590,
    rate: 25,
    amount: 397,
    status: "available",
  },
  {
    id: "COM-112",
    paymentId: 710033,
    clientUserId: 910010,
    client: nameOf(910010),
    clientHandle: handleOf(910010),
    created: "2026-07-02 09:41",
    gross: 790,
    rate: 25,
    amount: 197,
    status: "available",
  },
  {
    id: "COM-107",
    paymentId: 710028,
    clientUserId: 910017,
    client: nameOf(910017),
    clientHandle: handleOf(910017),
    created: "2026-06-29 20:12",
    gross: 4770,
    rate: 25,
    amount: 1192,
    status: "available",
  },
];

export const partnerLedger: PartnerLedgerRow[] = [
  {
    id: "LG-903",
    created: "2026-08-07 09:12",
    kindKey: "withdrawal_reserved",
    amount: -3500,
    balanceAfter: 12840,
    refId: "WD-502",
    refKind: "withdrawal",
  },
  {
    id: "LG-897",
    created: "2026-08-06 11:12",
    kindKey: "commission_available",
    amount: 477,
    balanceAfter: 16340,
    refId: "COM-184",
    refKind: "commission",
  },
  {
    id: "LG-884",
    created: "2026-08-01 14:03",
    kindKey: "commission_reversed",
    amount: -237,
    balanceAfter: 15863,
    refId: "COM-166",
    refKind: "commission",
  },
  {
    id: "LG-878",
    created: "2026-07-29 09:47",
    kindKey: "commission_accrued",
    amount: 954,
    balanceAfter: 13317,
    refId: "COM-158",
    refKind: "commission",
  },
  {
    id: "LG-877",
    created: "2026-07-26 16:18",
    kindKey: "commission_accrued",
    amount: 477,
    balanceAfter: 12363,
    refId: "COM-151",
    refKind: "commission",
  },
  {
    id: "LG-871",
    created: "2026-07-24 12:30",
    kindKey: "manual_adjustment",
    amount: 1200,
    balanceAfter: 16100,
    refId: "",
    refKind: "",
  },
  {
    id: "LG-869",
    created: "2026-07-23 10:05",
    kindKey: "commission_accrued",
    amount: 954,
    balanceAfter: 11886,
    refId: "COM-147",
    refKind: "commission",
  },
  {
    id: "LG-861",
    created: "2026-07-21 19:44",
    kindKey: "withdrawal_paid",
    amount: -5400,
    balanceAfter: 10932,
    refId: "WD-461",
    refKind: "withdrawal",
  },
  {
    id: "LG-852",
    created: "2026-07-20 21:44",
    kindKey: "commission_accrued",
    amount: 237,
    balanceAfter: 16332,
    refId: "COM-142",
    refKind: "commission",
  },
  {
    id: "LG-846",
    created: "2026-07-14 07:52",
    kindKey: "commission_accrued",
    amount: 1908,
    balanceAfter: 16095,
    refId: "COM-133",
    refKind: "commission",
  },
  {
    id: "LG-839",
    created: "2026-07-08 11:19",
    kindKey: "commission_reversed",
    amount: -954,
    balanceAfter: 14187,
    refId: "COM-124",
    refKind: "commission",
  },
  {
    id: "LG-831",
    created: "2026-07-05 15:03",
    kindKey: "commission_accrued",
    amount: 397,
    balanceAfter: 15141,
    refId: "COM-118",
    refKind: "commission",
  },
];

export const partnerAudit: PartnerAuditRow[] = [
  {
    id: "AU-311",
    created: "2026-08-06 10:04",
    actorUserId: 910001,
    actor: nameOf(910001),
    actionKey: "rate_changed",
    detail: "25% → 30%",
  },
  {
    id: "AU-298",
    created: "2026-07-24 12:30",
    actorUserId: 910001,
    actor: nameOf(910001),
    actionKey: "balance_adjusted",
    detail: "+1 200 ₽",
  },
  {
    id: "AU-274",
    created: "2026-06-11 15:22",
    actorUserId: 910001,
    actor: nameOf(910001),
    actionKey: "link_rotated",
    detail: "p_H3kQ9 → p_Q7m2pK8v4",
  },
  {
    id: "AU-190",
    created: "2026-04-18 09:00",
    actorUserId: 910001,
    actor: nameOf(910001),
    actionKey: "application_approved",
    detail: "APP-0912",
  },
  {
    id: "AU-256",
    created: "2026-05-30 09:18",
    actorUserId: 910001,
    actor: nameOf(910001),
    actionKey: "requisites_updated",
    detail: "SBP",
  },
  {
    id: "AU-241",
    created: "2026-05-12 16:44",
    actorUserId: 910001,
    actor: nameOf(910001),
    actionKey: "status_resumed",
    detail: "",
  },
  {
    id: "AU-229",
    created: "2026-04-28 11:02",
    actorUserId: 910001,
    actor: nameOf(910001),
    actionKey: "status_paused",
    detail: "",
  },
  {
    id: "AU-218",
    created: "2026-04-18 10:24",
    actorUserId: 910001,
    actor: nameOf(910001),
    actionKey: "partner_activated",
    detail: "30%",
  },
];

export const partnerLinks = [
  { id: "telegram", labelKey: "TG", url: "https://t.me/minishop_bot?start=p_Q7m2pK8v4" },
  { id: "web", labelKey: "WEB", url: "https://example.com/?start=p_Q7m2pK8v4" },
];

export type PartnerChartPoint = { date: string; amount: number };

function buildPartnerChartSeries(values: number[]): PartnerChartPoint[] {
  const start = Date.UTC(2025, 7, 8);
  return Array.from({ length: 365 }, (_, index) => ({
    date: new Date(start + index * 86_400_000).toISOString().slice(0, 10),
    amount: values[index % values.length] + ((index * 137) % 1900),
  }));
}

export const partnerRevenueDaily = buildPartnerChartSeries([
  6800, 7300, 8200, 7600, 9100, 10200, 8800, 11800, 9700, 12600, 10900, 13400,
]).map((point, index) => (index === 342 ? { ...point, amount: -2400 } : point));

export const partnerPayoutsDaily = buildPartnerChartSeries([
  0, 0, 3500, 0, 8200, 0, 3100, 0, 4800, 0, 9000, 0,
]);

export const pendingApplicationCount = applications.filter(
  (application) => application.status === "pending"
).length;

export const openWithdrawalCount = withdrawals.filter(
  (withdrawal) => withdrawal.status === "requested" || withdrawal.status === "processing"
).length;

export function partnerById(id: string): PartnerRow | undefined {
  return partners.find((partner) => partner.id.toLowerCase() === String(id).toLowerCase());
}

export type PartnerAttribution = {
  partnerId: string;
  partnerName: string;
  partnerHandle: string;
  rate: number;
  amount: number;
  status: PartnerCommissionRow["status"];
  commissionId: string;
};

/**
 * Partner attribution for a payment, for the payment card. Every commission in
 * the preview points at a real payment id, so the card can show who earned on
 * this payment and how much without a second source of truth.
 */
export function partnerAttributionForPayment(paymentId: unknown): PartnerAttribution | null {
  const id = Number(paymentId);
  if (!Number.isFinite(id)) return null;
  const commission = partnerCommissions.find((entry) => entry.paymentId === id);
  if (!commission) return null;
  // Every commission in the preview belongs to the partner whose card links here.
  const owner = partners[0];
  return {
    partnerId: owner.id,
    partnerName: owner.name,
    partnerHandle: owner.handle,
    rate: commission.rate,
    amount: commission.amount,
    status: commission.status,
    commissionId: commission.id,
  };
}

export function withdrawalById(id: string): WithdrawalRow | undefined {
  return withdrawals.find((withdrawal) => withdrawal.id.toLowerCase() === String(id).toLowerCase());
}
