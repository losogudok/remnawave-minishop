import { describe, expect, it } from "vitest";

import { revenueChartGradientFallbacks } from "./revenueChartColors.js";

describe("revenueChartGradientFallbacks", () => {
  it("derives the default fill from the effective chart stroke", () => {
    expect(revenueChartGradientFallbacks("#38bdf8")).toEqual({
      start: "rgba(56, 189, 248, 0.38)",
      end: "rgba(56, 189, 248, 0)",
    });
  });

  it("preserves an explicitly configured fill", () => {
    expect(revenueChartGradientFallbacks("#00fe7a", "rgba(1, 2, 3, 0.25)")).toEqual({
      start: "rgba(1, 2, 3, 0.25)",
      end: "rgba(0, 254, 122, 0)",
    });
  });

  it("supports short hex and other CSS color formats", () => {
    expect(revenueChartGradientFallbacks("#0af")).toEqual({
      start: "rgba(0, 170, 255, 0.38)",
      end: "rgba(0, 170, 255, 0)",
    });
    expect(revenueChartGradientFallbacks("oklch(70% 0.2 200)")).toEqual({
      start: "color-mix(in srgb, oklch(70% 0.2 200) 38%, transparent)",
      end: "color-mix(in srgb, oklch(70% 0.2 200) 0%, transparent)",
    });
  });
});
