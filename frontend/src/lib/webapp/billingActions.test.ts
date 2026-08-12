import { describe, expect, it, vi } from "vitest";

import { createBillingActions } from "./billingActions.js";

describe("billingActions partner balance funding", () => {
  it("includes the selection in every supported checkout payload", () => {
    const actions = createBillingActions({ api: vi.fn() });
    const plan = {
      months: 3,
      traffic_gb: 50,
      device_count: 2,
      tariff_key: "pro",
      sale_mode: "subscription@pro",
    };

    expect(
      actions.planPaymentBody(plan, "card", {
        usePartnerBalance: true,
      })
    ).toMatchObject({ use_partner_balance: true });
    expect(actions.topupPaymentBody(plan, "card", "pro", null, true)).toMatchObject({
      use_partner_balance: true,
    });
    expect(actions.deviceTopupPaymentBody(plan, "card", "pro", null, true)).toMatchObject({
      use_partner_balance: true,
    });
    expect(
      actions.changePaymentBody(
        { mode: "buy_period", months: 3 },
        { tariff_key: "pro" },
        "card",
        true
      )
    ).toMatchObject({ use_partner_balance: true });
  });
});
