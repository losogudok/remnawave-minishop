import { describe, expect, it } from "vitest";

import { resolveProgramEntryPlacement } from "./programEntryPolicy.js";

describe("program entry placement", () => {
  it.each([
    {
      expected: {
        bonusesNavigationVisible: true,
        partnerNavigationVisible: false,
        partnerSettingsVisible: false,
        promoSettingsVisible: false,
      },
      partnerProgramEnabled: false,
      referralProgramEnabled: true,
    },
    {
      expected: {
        bonusesNavigationVisible: false,
        partnerNavigationVisible: false,
        partnerSettingsVisible: false,
        promoSettingsVisible: true,
      },
      partnerProgramEnabled: false,
      referralProgramEnabled: false,
    },
    {
      expected: {
        bonusesNavigationVisible: false,
        partnerNavigationVisible: true,
        partnerSettingsVisible: false,
        promoSettingsVisible: true,
      },
      partnerProgramEnabled: true,
      referralProgramEnabled: false,
    },
    {
      expected: {
        bonusesNavigationVisible: true,
        partnerNavigationVisible: false,
        partnerSettingsVisible: true,
        promoSettingsVisible: false,
      },
      partnerProgramEnabled: true,
      referralProgramEnabled: true,
    },
  ])(
    "places entries for referral=$referralProgramEnabled and partner=$partnerProgramEnabled",
    ({ expected, partnerProgramEnabled, referralProgramEnabled }) => {
      expect(
        resolveProgramEntryPlacement({ partnerProgramEnabled, referralProgramEnabled })
      ).toEqual(expected);
    }
  );
});
