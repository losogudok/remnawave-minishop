import { partnerSortColumns } from "$lib/admin/partnerProgramSort.js";
import { sortAdminRows } from "$lib/admin/tableSort.js";
import { stripRoutePrefix } from "$lib/webapp/routes.js";
import {
  applications as previewApplications,
  partnerAudit as previewPartnerAudit,
  partnerClients as previewPartnerClients,
  partnerCommissions as previewPartnerCommissions,
  partnerLedger as previewPartnerLedger,
  partnerLinks as previewPartnerLinks,
  partnerPayoutsDaily as previewPartnerPayoutsDaily,
  partnerRevenueDaily as previewPartnerRevenueDaily,
  partners as previewPartners,
  withdrawals as previewWithdrawals,
  type ApplicationRow,
  type PartnerAuditRow,
  type PartnerChartPoint,
  type PartnerClientRow,
  type PartnerCommissionRow,
  type PartnerLedgerRow,
  type PartnerRow,
  type WithdrawalRow,
} from "$lib/admin/previewMock/partnerProgram.js";
import type { PartnerLinkRow } from "$lib/admin/partnerProgramApi.js";

export type PartnerAdminView =
  | "dashboard"
  | "partners"
  | "applications"
  | "withdrawals"
  | "partner_detail"
  | "application_detail"
  | "withdrawal_detail";
export type PartnerDialogKind = "" | "create" | "rate" | "balance" | "import" | "status" | "link";

export const emptyPartner: PartnerRow = {
  id: "",
  userId: 0,
  name: "",
  handle: "",
  avatarUrl: "",
  status: "closed",
  rate: 0,
  clients: 0,
  payments: 0,
  gross: 0,
  earned: 0,
  available: 0,
  activated: "",
};
export const emptyApplication: ApplicationRow = {
  id: "",
  userId: 0,
  user: "",
  handle: "",
  submitted: "",
  status: "canceled",
  messageKey: "",
};
export const emptyWithdrawal: WithdrawalRow = {
  id: "",
  partnerId: "",
  partner: "",
  handle: "",
  method: "bank_card",
  masked: "",
  amount: 0,
  status: "failed",
  requested: "",
  processedAt: "",
  noteKey: "",
};

export function initialPartnerAdminScenario(): string {
  if (typeof window === "undefined") return "populated";
  return String(
    new URLSearchParams(window.location.search).get("partner_admin_scenario") || ""
  ).toLowerCase();
}

export function partnerAdminRouteState(routePrefix: string): {
  id: string;
  view: PartnerAdminView;
} {
  if (typeof window === "undefined") return { id: "", view: "dashboard" };
  const path = stripRoutePrefix(window.location.pathname, routePrefix);
  const detailMatch = path.match(
    /^\/admin\/partners\/(partner|applications|withdrawals)\/([^/]+)$/i
  );
  if (detailMatch) {
    const detailView: Record<string, PartnerAdminView> = {
      applications: "application_detail",
      partner: "partner_detail",
      withdrawals: "withdrawal_detail",
    };
    return {
      id: decodeURIComponent(detailMatch[2]),
      view: detailView[detailMatch[1].toLowerCase()] || "dashboard",
    };
  }
  const listMatch = path.match(/^\/admin\/partners\/(partners|applications|withdrawals)$/i);
  if (listMatch) return { id: "", view: listMatch[1].toLowerCase() as PartnerAdminView };
  const queryView = new URLSearchParams(window.location.search).get("partner_admin_view");
  if (queryView === "partners" || queryView === "applications" || queryView === "withdrawals")
    return { id: "", view: queryView };
  if (
    queryView === "partner_detail" ||
    queryView === "application_detail" ||
    queryView === "withdrawal_detail"
  )
    return { id: "", view: queryView };
  return { id: "", view: "dashboard" };
}

export function createPartnerAdminPreviewState(
  previewMode: boolean,
  emptyCharts: boolean,
  emptyLists: boolean
) {
  return {
    partners: previewMode ? previewPartners.map((item) => ({ ...item })) : ([] as PartnerRow[]),
    topPartners:
      previewMode && !emptyLists
        ? sortAdminRows(previewPartners, "earned_desc", partnerSortColumns).slice(0, 6)
        : ([] as PartnerRow[]),
    applications: previewMode
      ? previewApplications.map((item) => ({ ...item }))
      : ([] as ApplicationRow[]),
    withdrawals:
      previewMode && !emptyLists
        ? previewWithdrawals.map((item) => ({ ...item }))
        : ([] as WithdrawalRow[]),
    partnerLinks: previewMode
      ? previewPartnerLinks.map((item) => ({ ...item, id: item.id as PartnerLinkRow["id"] }))
      : ([] as PartnerLinkRow[]),
    partnerClients: previewMode ? [...previewPartnerClients] : ([] as PartnerClientRow[]),
    partnerCommissions: previewMode
      ? [...previewPartnerCommissions]
      : ([] as PartnerCommissionRow[]),
    partnerLedger: previewMode ? [...previewPartnerLedger] : ([] as PartnerLedgerRow[]),
    partnerAudit: previewMode ? [...previewPartnerAudit] : ([] as PartnerAuditRow[]),
    partnerRevenueDaily: previewMode
      ? previewPartnerRevenueDaily.map((item) =>
          emptyCharts ? { ...item, amount: 0 } : { ...item }
        )
      : ([] as PartnerChartPoint[]),
    partnerPayoutsDaily: previewMode
      ? previewPartnerPayoutsDaily.map((item) =>
          emptyCharts ? { ...item, amount: 0 } : { ...item }
        )
      : ([] as PartnerChartPoint[]),
  };
}
