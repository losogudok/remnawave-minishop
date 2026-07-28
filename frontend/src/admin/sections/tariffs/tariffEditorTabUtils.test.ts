import { describe, expect, it } from "vitest";

import { tributeEnabled, tributeSectionVisible } from "./tariffEditorTabUtils";
import type { TariffsStore } from "$lib/admin/stores/tariffsStore";

function store(enabled: boolean): TariffsStore {
  return {
    providerCurrencySupport: [{ id: "tribute", enabled }],
  } as unknown as TariffsStore;
}

const emptyDraft = {
  tributeLink: "",
  tributeSubscriptionId: "",
  periodRows: [{ months: 1, tribute_link: "", tribute_subscription_id: "", tribute_period_id: "" }],
  topupRows: [{ gb: 10, tribute_product_id: "", tribute_product_link: "" }],
  premiumTopupRows: [],
  trafficRows: [],
};

describe("tributeSectionVisible", () => {
  it("follows the provider while the tariff carries no mapping", () => {
    expect(tributeEnabled(store(true))).toBe(true);
    expect(tributeSectionVisible(store(true), emptyDraft)).toBe(true);
    expect(tributeSectionVisible(store(false), emptyDraft)).toBe(false);
  });

  it("keeps a leftover period binding reachable after the provider is off", () => {
    // Hiding it left the value in the payload with no field to clear it in,
    // and the backend refuses the whole catalog over a half-filled binding.
    const draft = {
      ...emptyDraft,
      periodRows: [
        { months: 1, tribute_link: "", tribute_subscription_id: "", tribute_period_id: 1001 },
      ],
    };

    expect(tributeSectionVisible(store(false), draft)).toBe(true);
  });

  it("keeps a leftover product binding reachable after the provider is off", () => {
    const draft = {
      ...emptyDraft,
      premiumTopupRows: [{ gb: 50, tribute_product_id: 501, tribute_product_link: "" }],
    };

    expect(tributeSectionVisible(store(false), draft)).toBe(true);
  });

  it("keeps a tariff-level subscription reachable after the provider is off", () => {
    const draft = { ...emptyDraft, tributeLink: "https://web.tribute.tg/s/11Pv" };

    expect(tributeSectionVisible(store(false), draft)).toBe(true);
  });

  it("survives a draft without row arrays", () => {
    expect(tributeSectionVisible(store(false), {})).toBe(false);
    expect(tributeSectionVisible(store(false), null)).toBe(false);
  });
});
