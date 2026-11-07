import { LANGUAGE_LABELS, normalizeLanguageCode, resolveLocaleKey } from "./constants";
import { formatTemplate, formatFraction, roundToHalf } from "./formatters.js";
import { unitPluralBucket } from "./plurals.js";

type MessageBucket = Record<string, unknown>;
export type I18nMessages = Record<string, unknown>;

export type I18nOptions = {
  messages?: I18nMessages;
  defaultLang?: string;
  getLang?: (() => string) | null;
  supportedLanguages?: string[];
};

const LANGUAGE_LABEL_MAP: Record<string, string> = LANGUAGE_LABELS;

export function createI18n({
  messages: initialMessages = {},
  defaultLang = "ru",
  getLang = null,
  supportedLanguages = [],
}: I18nOptions = {}) {
  const messages: Record<string, MessageBucket> = {};
  const supported = new Set(supportedLanguages.map(normalizeLanguageCode));

  function mergeMessages(nextMessages: I18nMessages = {}): Record<string, MessageBucket> {
    if (!nextMessages || typeof nextMessages !== "object") return messages;
    for (const [lang, bucket] of Object.entries(nextMessages)) {
      if (!bucket || typeof bucket !== "object") continue;
      messages[lang] = { ...(messages[lang] || {}), ...(bucket as MessageBucket) };
    }
    return messages;
  }

  mergeMessages(initialMessages);

  function normalizeLangCode(lang: unknown): string {
    const key = normalizeLanguageCode(lang);
    if (!key) return defaultLang;
    const base = key.split("-")[0];
    if (messages[key]) return key;
    if (messages[base]) return base;
    if (supported.has(key)) return key;
    if (supported.has(base)) return base;
    if (LANGUAGE_LABEL_MAP[key]) return key;
    if (LANGUAGE_LABEL_MAP[base]) return base;
    return defaultLang;
  }

  function currentLang(): string {
    return normalizeLangCode(typeof getLang === "function" ? getLang() : defaultLang);
  }

  function t(key: string, params: Record<string, unknown> = {}, fallback = ""): string {
    const lang = currentLang();
    const lookupKey = resolveLocaleKey(key);
    const langMessages = messages?.[lang];
    if (
      langMessages &&
      Object.prototype.hasOwnProperty.call(langMessages, lookupKey) &&
      typeof langMessages[lookupKey] === "string"
    ) {
      const raw = langMessages[lookupKey] as string;
      // An explicitly emptied string is an intentional "hide this copy" signal
      // from the admin translations panel. Return it as-is instead of falling
      // through to another language or the fallback literal, so callers can
      // hide the block (backend gettext already preserves empty overrides).
      if (!raw.length) return raw;
      return formatTemplate(raw, params);
    }
    const variants = [messages?.en?.[lookupKey], messages?.ru?.[lookupKey], fallback, key];
    const raw = variants.find((value) => typeof value === "string" && value.length);
    return formatTemplate(raw, params);
  }

  function languageName(code: unknown): string {
    const key = normalizeLanguageCode(code);
    if (!key) return t("wa_language_default");
    return LANGUAGE_LABEL_MAP[key] || key.toUpperCase();
  }

  function termUnitLabel(value: unknown, unit: unknown): string {
    const bucket = unitPluralBucket(value, currentLang());
    return t(`wa_sub_term_${unit}_${bucket}`);
  }

  return { normalizeLangCode, t, currentLang, languageName, termUnitLabel, mergeMessages };
}

export { formatFraction, roundToHalf };
