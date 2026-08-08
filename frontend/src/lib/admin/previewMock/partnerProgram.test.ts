import { describe, expect, it } from "vitest";

import {
  applications,
  partnerAudit,
  partnerClients,
  partnerCommissions,
  partnerLedger,
  partners,
  withdrawals,
} from "./partnerProgram.js";

/**
 * Every list here is rendered by a keyed `{#each}`. A duplicate id does not
 * merely look wrong — Svelte throws `each_key_duplicate` and the whole tab
 * stops rendering, which is exactly how the ledger tab silently broke once.
 */
describe("partner preview mock", () => {
  const lists = {
    partners,
    applications,
    withdrawals,
    partnerClients,
    partnerCommissions,
    partnerLedger,
    partnerAudit,
  };

  for (const [name, rows] of Object.entries(lists)) {
    it(`${name} has unique ids`, () => {
      const ids = rows.map((row) => row.id);
      expect(new Set(ids).size, `duplicate id in ${name}`).toBe(ids.length);
    });

    it(`${name} is not empty`, () => {
      expect(rows.length).toBeGreaterThan(0);
    });
  }

  it("commissions point at partners that exist", () => {
    const partnerIds = new Set(partners.map((partner) => partner.id));
    for (const withdrawal of withdrawals) {
      expect(partnerIds.has(withdrawal.partnerId), withdrawal.id).toBe(true);
    }
  });
});
