import type { BillingPlan } from "./tariffs.js";

type SelectPaymentMethod = (methodId: string) => void;

export function selectPaymentMethodWithPromoReset(
  methodId: string,
  plan: BillingPlan | null,
  selectMethod: SelectPaymentMethod,
  clearCheckoutPromo: () => void
): void {
  selectMethod(methodId);
  const saleMode = String(plan?.sale_mode || "subscription").toLowerCase();
  if (String(methodId || "").toLowerCase() === "tribute" && saleMode === "subscription") {
    clearCheckoutPromo();
  }
}

export function checkoutPromoAffectsQuotedPlan(
  discount: number,
  scopeMatches: boolean,
  thresholdMatches: boolean
): boolean {
  return discount > 0 && scopeMatches && thresholdMatches;
}

export function checkoutPromoBlockVisible(
  providerManagesPrice: boolean,
  hasSelectionOrPromoState: boolean
): boolean {
  return !providerManagesPrice && hasSelectionOrPromoState;
}
