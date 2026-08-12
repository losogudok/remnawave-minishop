import type {
  ApplicationRow,
  PartnerRow,
  WithdrawalRow,
} from "$lib/admin/previewMock/partnerProgram.js";
import type { AdminSortColumn } from "$lib/admin/tableSort.js";

export const partnerSortColumns: readonly AdminSortColumn<PartnerRow>[] = [
  {
    asc: "user_asc",
    desc: "user_desc",
    defaultDirection: "asc",
    value: (row) => [row.name, row.handle],
  },
  { asc: "status_asc", desc: "status_desc", defaultDirection: "asc", value: (row) => row.status },
  { asc: "rate_asc", desc: "rate_desc", defaultDirection: "desc", value: (row) => row.rate },
  {
    asc: "clients_asc",
    desc: "clients_desc",
    defaultDirection: "desc",
    value: (row) => row.clients,
  },
  { asc: "gross_asc", desc: "gross_desc", defaultDirection: "desc", value: (row) => row.gross },
  {
    asc: "earned_asc",
    desc: "earned_desc",
    defaultDirection: "desc",
    value: (row) => row.earned,
  },
  {
    asc: "available_asc",
    desc: "available_desc",
    defaultDirection: "desc",
    value: (row) => row.available,
  },
];

export const applicationSortColumns: readonly AdminSortColumn<ApplicationRow>[] = [
  {
    asc: "application_asc",
    desc: "application_desc",
    defaultDirection: "desc",
    value: (row) => row.id,
  },
  {
    asc: "applicant_asc",
    desc: "applicant_desc",
    defaultDirection: "asc",
    value: (row) => [row.user, row.handle],
  },
  {
    asc: "message_asc",
    desc: "message_desc",
    defaultDirection: "asc",
    value: (row) => row.messageKey,
  },
  {
    asc: "submitted_asc",
    desc: "submitted_desc",
    defaultDirection: "desc",
    value: (row) => row.submitted,
  },
  {
    asc: "application_status_asc",
    desc: "application_status_desc",
    defaultDirection: "asc",
    value: (row) => row.status,
  },
];

export const withdrawalSortColumns: readonly AdminSortColumn<WithdrawalRow>[] = [
  {
    asc: "withdrawal_asc",
    desc: "withdrawal_desc",
    defaultDirection: "desc",
    value: (row) => row.id,
  },
  {
    asc: "withdrawal_partner_asc",
    desc: "withdrawal_partner_desc",
    defaultDirection: "asc",
    value: (row) => [row.partner, row.handle],
  },
  { asc: "method_asc", desc: "method_desc", defaultDirection: "asc", value: (row) => row.method },
  {
    asc: "amount_asc",
    desc: "amount_desc",
    defaultDirection: "desc",
    value: (row) => row.amount,
  },
  {
    asc: "withdrawal_status_asc",
    desc: "withdrawal_status_desc",
    defaultDirection: "asc",
    value: (row) => row.status,
  },
  {
    asc: "requested_asc",
    desc: "requested_desc",
    defaultDirection: "desc",
    value: (row) => row.requested,
  },
];
