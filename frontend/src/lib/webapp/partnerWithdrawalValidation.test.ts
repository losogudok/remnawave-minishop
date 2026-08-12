import { describe, expect, it } from "vitest";

import {
  normalizePartnerWithdrawalRequisites,
  partnerWithdrawalRequisitesError,
} from "./partnerWithdrawalValidation.js";

describe("partner withdrawal requisites", () => {
  it("normalizes and validates card numbers with Luhn", () => {
    expect(normalizePartnerWithdrawalRequisites("bank_card", "4111 1111-1111 1111")).toBe(
      "4111111111111111"
    );
    expect(partnerWithdrawalRequisitesError("bank_card", "4111 1111 1111 1111")).toBeNull();
    expect(partnerWithdrawalRequisitesError("bank_card", "4111 1111 1111 1112")).toBe(
      "invalid_card_number"
    );
  });

  it("normalizes Russian mobile numbers and validates E.164", () => {
    expect(normalizePartnerWithdrawalRequisites("sbp", "8 (999) 123-45-67")).toBe("+79991234567");
    expect(partnerWithdrawalRequisitesError("sbp", "8 (999) 123-45-67")).toBeNull();
    expect(partnerWithdrawalRequisitesError("sbp", "12345")).toBe("invalid_phone");
  });

  it("accepts broad visible crypto address alphabets but rejects hidden text", () => {
    expect(
      partnerWithdrawalRequisitesError("crypto", "EQAbcdefghijklmnopqrstuvwxyz0123456789_-/+=")
    ).toBeNull();
    expect(partnerWithdrawalRequisitesError("crypto", "wallet address")).toBe(
      "invalid_crypto_address"
    );
    expect(partnerWithdrawalRequisitesError("crypto", "wallet\u200baddress")).toBe(
      "invalid_crypto_address"
    );
  });
});
