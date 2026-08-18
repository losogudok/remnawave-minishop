import { describe, expect, it } from "vitest";

import { adConversionCount, adRegistrationCount } from "./adStats";

describe("adStats", () => {
  it("maps backend campaign stats to the admin table counters", () => {
    const stats = { starts: 7, trials: 3, payers: 2, revenue: 450 };

    expect(adRegistrationCount(stats)).toBe(7);
    expect(adConversionCount(stats)).toBe(2);
  });

  it("falls back to zero when campaign stats are absent", () => {
    expect(adRegistrationCount(undefined)).toBe(0);
    expect(adConversionCount(undefined)).toBe(0);
  });
});
