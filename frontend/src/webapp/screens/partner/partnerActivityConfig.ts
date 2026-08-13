import { type AdminSortColumn } from "$lib/admin/tableSort.js";
import type {
  PartnerClientPreview,
  PartnerCommissionPreview,
  PartnerWithdrawalPreview,
} from "$lib/webapp/previewMock/partnerProgram.js";
import type { PartnerTourStep } from "./partnerTourTypes";

export const APPLICATION_MIN = 10;
export const ACTIVITY_PAGE_SIZE = 20;
export type ActivityTab = "clients" | "commissions" | "withdrawals";

export const clientColumns: AdminSortColumn<PartnerClientPreview>[] = [
  { asc: "client_asc", desc: "client_desc", defaultDirection: "asc", value: (row) => row.label },
  {
    asc: "attributed_asc",
    desc: "attributed_desc",
    defaultDirection: "desc",
    value: (row) => row.attributedAt,
  },
  { asc: "source_asc", desc: "source_desc", defaultDirection: "asc", value: (row) => row.source },
  {
    asc: "payments_asc",
    desc: "payments_desc",
    defaultDirection: "desc",
    value: (row) => row.payments,
  },
  { asc: "gross_asc", desc: "gross_desc", defaultDirection: "desc", value: (row) => row.gross },
];
export const commissionColumns: AdminSortColumn<PartnerCommissionPreview>[] = [
  {
    asc: "client_asc",
    desc: "client_desc",
    defaultDirection: "asc",
    value: (row) => row.clientLabel,
  },
  {
    asc: "created_asc",
    desc: "created_desc",
    defaultDirection: "desc",
    value: (row) => row.createdAt,
  },
  { asc: "basis_asc", desc: "basis_desc", defaultDirection: "desc", value: (row) => row.gross },
  {
    asc: "amount_asc",
    desc: "amount_desc",
    defaultDirection: "desc",
    value: (row) => row.amount,
  },
  { asc: "status_asc", desc: "status_desc", defaultDirection: "asc", value: (row) => row.status },
];
export const withdrawalColumns: AdminSortColumn<PartnerWithdrawalPreview>[] = [
  { asc: "method_asc", desc: "method_desc", defaultDirection: "asc", value: (row) => row.method },
  {
    asc: "created_asc",
    desc: "created_desc",
    defaultDirection: "desc",
    value: (row) => row.createdAt,
  },
  {
    asc: "amount_asc",
    desc: "amount_desc",
    defaultDirection: "desc",
    value: (row) => row.amount,
  },
  { asc: "status_asc", desc: "status_desc", defaultDirection: "asc", value: (row) => row.status },
];
export const defaultSortByTab: Record<ActivityTab, string> = {
  clients: "gross_desc",
  commissions: "created_desc",
  withdrawals: "created_desc",
};

// Every step points at a real control on this screen, so the coach mark
// explains what the partner is actually looking at.
export const tourSteps: PartnerTourStep[] = [
  {
    target: "links",
    titleKey: "wa_partner_tutorial_1_title",
    textKey: "wa_partner_tutorial_1_text",
  },
  {
    target: "balance",
    titleKey: "wa_partner_tutorial_2_title",
    textKey: "wa_partner_tutorial_2_text",
  },
  {
    target: "clients",
    titleKey: "wa_partner_tutorial_3_title",
    textKey: "wa_partner_tutorial_3_text",
  },
  {
    target: "withdraw",
    titleKey: "wa_partner_tutorial_4_title",
    textKey: "wa_partner_tutorial_4_text",
  },
];
