export type PartnerWithdrawalMethodType = "bank_card" | "sbp" | "crypto";
export type PartnerWithdrawalRequisitesError =
  "invalid_card_number" | "invalid_phone" | "invalid_crypto_address" | null;

const CARD_DIGITS = /^\d{12,19}$/;
const PHONE_E164 = /^\+[1-9]\d{7,14}$/;
const HIDDEN_OR_WHITESPACE = /[\s\p{C}]/u;

function luhnValid(value: string): boolean {
  let total = 0;
  const parity = value.length % 2;
  for (let index = 0; index < value.length; index += 1) {
    let digit = Number(value[index]);
    if (index % 2 === parity) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    total += digit;
  }
  return total % 10 === 0;
}

export function normalizePartnerWithdrawalRequisites(
  type: PartnerWithdrawalMethodType,
  raw: string
): string {
  const value = raw.trim();
  if (type === "bank_card") return value.replace(/[\s-]+/g, "");
  if (type === "sbp") {
    const phone = value.replace(/[\s()-]+/g, "");
    return phone.startsWith("8") && phone.length === 11 ? `+7${phone.slice(1)}` : phone;
  }
  return value;
}

export function partnerWithdrawalRequisitesError(
  type: PartnerWithdrawalMethodType,
  raw: string
): PartnerWithdrawalRequisitesError {
  const value = normalizePartnerWithdrawalRequisites(type, raw);
  if (!value) return null;
  if (type === "bank_card") {
    return CARD_DIGITS.test(value) && luhnValid(value) ? null : "invalid_card_number";
  }
  if (type === "sbp") return PHONE_E164.test(value) ? null : "invalid_phone";
  return value.length >= 4 && value.length <= 256 && !HIDDEN_OR_WHITESPACE.test(value)
    ? null
    : "invalid_crypto_address";
}
