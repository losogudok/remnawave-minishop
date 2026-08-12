type SectionAvailabilityInput = {
  devicesEnabled?: boolean;
  installGuidesAvailable?: boolean;
  isAdmin?: boolean;
  partnerProgramEnabled?: boolean;
  section: string;
  supportEnabled?: boolean;
};

export function resolveAvailableWebappSection({
  devicesEnabled = false,
  installGuidesAvailable = false,
  isAdmin = false,
  partnerProgramEnabled = false,
  section,
  supportEnabled = true,
}: SectionAvailabilityInput) {
  if (section === "admin" && !isAdmin) return "settings";
  if (section === "devices" && !devicesEnabled) return "home";
  if (section === "partner" && !partnerProgramEnabled) return "home";
  if (section === "support" && !supportEnabled) return "home";
  if (section === "install" && !installGuidesAvailable) return "home";
  return section;
}

export function activeTabForWebappSection(section: string) {
  if (section === "admin") return "settings";
  if (section === "install" || section === "trial") return "home";
  return section;
}
