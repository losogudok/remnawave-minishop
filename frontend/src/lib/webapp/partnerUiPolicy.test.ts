import { describe, expect, it } from "vitest";

import { partnerLoadingPlaceholder, shouldShowPartnerBalanceDiscount } from "./partnerUiPolicy.js";

describe("partner UI policy", () => {
  it("keeps the balance option hidden until a positive discount is confirmed", () => {
    const checkout = {
      open: true,
      eligible: true,
      currency: "RUB",
      maximumDiscount: 0,
    };

    expect(shouldShowPartnerBalanceDiscount(checkout)).toBe(false);
    expect(shouldShowPartnerBalanceDiscount({ ...checkout, maximumDiscount: 120 })).toBe(true);
  });

  it("keeps unavailable checkout states hidden", () => {
    expect(
      shouldShowPartnerBalanceDiscount({
        open: true,
        eligible: false,
        currency: "RUB",
        maximumDiscount: 120,
      })
    ).toBe(false);
    expect(
      shouldShowPartnerBalanceDiscount({
        open: false,
        eligible: true,
        currency: "RUB",
        maximumDiscount: 120,
      })
    ).toBe(false);
  });

  it("uses the partner dashboard skeleton only in explicit preview mode", () => {
    expect(partnerLoadingPlaceholder(false)).toBe("neutral");
    expect(partnerLoadingPlaceholder(true)).toBe("dashboard");
  });
});
