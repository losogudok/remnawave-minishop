import { describe, expect, it } from "vitest";

import { resolveProgramEntryPlacement } from "./programEntryPolicy.js";

describe("program entry placement", () => {
  it.each([
    {
      expected: {
        bonusesNavigationVisible: true,
        partnerNavigationVisible: false,
        partnerSettingsVisible: false,
      },
      partnerProgramEnabled: false,
      referralProgramEnabled: true,
    },
    {
      expected: {
        bonusesNavigationVisible: false,
        partnerNavigationVisible: false,
        partnerSettingsVisible: false,
      },
      partnerProgramEnabled: false,
      referralProgramEnabled: false,
    },
    {
      expected: {
        bonusesNavigationVisible: false,
        partnerNavigationVisible: true,
        partnerSettingsVisible: false,
      },
      partnerProgramEnabled: true,
      referralProgramEnabled: false,
    },
    {
      expected: {
        bonusesNavigationVisible: true,
        partnerNavigationVisible: false,
        partnerSettingsVisible: true,
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
