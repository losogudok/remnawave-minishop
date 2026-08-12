import { describe, expect, it } from "vitest";

import { shouldShowPartnerReferralImport } from "./partnerProgramUi.js";

describe("partner referral import visibility", () => {
  it("hides the banner after every referral was converted", () => {
    expect(shouldShowPartnerReferralImport(0, 0)).toBe(false);
  });

  it("keeps the banner for actionable imports, conflicts, and preview fixtures", () => {
    expect(shouldShowPartnerReferralImport(1, 0)).toBe(true);
    expect(shouldShowPartnerReferralImport(0, 1)).toBe(true);
    expect(shouldShowPartnerReferralImport(0, 0, true)).toBe(true);
  });
});
