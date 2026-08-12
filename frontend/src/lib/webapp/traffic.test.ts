import { describe, expect, it } from "vitest";

import { premiumTrafficLabel, premiumTrafficLimitVisible } from "./traffic.js";

describe("premium traffic quota presentation", () => {
  const t = (_key: string, params?: Record<string, unknown>) =>
    `${String(params?.used || "")} / ${String(params?.limit || "")}`;

  it("shows an explicit zero quota when the tariff is limited", () => {
    const subscription = {
      premium_traffic_limited: true,
      premium_limit_bytes: 0,
      premium_used_bytes: 0,
      premium_used: "0 GB",
    };

    expect(premiumTrafficLimitVisible(subscription)).toBe(true);
    expect(premiumTrafficLabel(subscription, t)).toBe("0 GB / 0 GB");
  });

  it("hides the quota card for unlimited premium access", () => {
    expect(
      premiumTrafficLimitVisible({
        premium_traffic_limited: false,
        premium_limit_bytes: 0,
      })
    ).toBe(false);
  });
});
