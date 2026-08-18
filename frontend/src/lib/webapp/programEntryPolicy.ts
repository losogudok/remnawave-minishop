export type ProgramEntryPlacement = {
  bonusesNavigationVisible: boolean;
  partnerNavigationVisible: boolean;
  partnerSettingsVisible: boolean;
  promoSettingsVisible: boolean;
};

export function resolveProgramEntryPlacement({
  partnerProgramEnabled,
  referralProgramEnabled,
}: {
  partnerProgramEnabled: boolean;
  referralProgramEnabled: boolean;
}): ProgramEntryPlacement {
  return {
    bonusesNavigationVisible: referralProgramEnabled,
    partnerNavigationVisible: partnerProgramEnabled && !referralProgramEnabled,
    partnerSettingsVisible: partnerProgramEnabled && referralProgramEnabled,
    promoSettingsVisible: !referralProgramEnabled,
  };
}
