export type PartnerStatusVariant = "success" | "warning" | "danger" | "muted";

export function formatPartnerMoney(value: number, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(value);
}

export function partnerStatusVariant(status: string): PartnerStatusVariant {
  if (status === "active" || status === "approved" || status === "paid") return "success";
  if (status === "pending" || status === "requested" || status === "processing") return "warning";
  if (status === "rejected" || status === "closed") return "danger";
  return "muted";
}
