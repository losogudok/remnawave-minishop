import type { WebappRecord } from "./types";

export function emptyCheckoutPromoQuote() {
  return {
    checkoutPromoDiscountPercent: 0,
    checkoutPromoAppliesTo: "all",
    checkoutPromoMinSubscriptionMonths: null,
    checkoutPromoMinTrafficGb: null,
  };
}

export function suggestedCheckoutPromoPatch(
  state: { checkoutPromoInput: string; checkoutPromoAppliedCode: string },
  options: WebappRecord
) {
  const code = String(options?.suggestedPromoCode || "").trim();
  const existing = String(state.checkoutPromoInput || state.checkoutPromoAppliedCode || "").trim();
  if (!code || existing) return { checkoutPromoAutoApply: false };
  return {
    checkoutPromoInput: code,
    checkoutPromoAutoApply: true,
    checkoutPromoAppliedCode: "",
    checkoutPromoStatus: "",
    checkoutPromoIsError: false,
    checkoutPromoPriceText: "",
    ...emptyCheckoutPromoQuote(),
  };
}
