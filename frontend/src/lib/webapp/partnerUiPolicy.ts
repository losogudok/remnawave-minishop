export type PartnerBalanceVisibility = {
  open: boolean;
  eligible: boolean;
  currency: string;
  maximumDiscount: number;
};

export function shouldShowPartnerBalanceDiscount({
  open,
  eligible,
  currency,
  maximumDiscount,
}: PartnerBalanceVisibility): boolean {
  return open && eligible && Boolean(currency) && maximumDiscount > 0;
}

export function partnerLoadingPlaceholder(previewMode: boolean): "dashboard" | "neutral" {
  return previewMode ? "dashboard" : "neutral";
}
