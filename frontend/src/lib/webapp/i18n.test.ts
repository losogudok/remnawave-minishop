import { describe, expect, it } from "vitest";

import { createI18n } from "./i18n.js";

describe("i18n language loading", () => {
  it("accepts a configured language before its dictionary has loaded", () => {
    let selectedLanguage = "ru";
    const i18n = createI18n({
      messages: { ru: { wa_title: "Название" } },
      supportedLanguages: ["ru", "it"],
      getLang: () => selectedLanguage,
    });

    expect(i18n.normalizeLangCode("it")).toBe("it");
    selectedLanguage = "it";
    i18n.mergeMessages({ it: { wa_title: "Titolo" } });
    expect(i18n.t("wa_title")).toBe("Titolo");
  });

  it("falls back through English and Russian before the literal fallback", () => {
    const i18n = createI18n({
      messages: {
        ru: { wa_login_intro: "Войдите, чтобы управлять подпиской." },
        en: { wa_login_intro: "Sign in to manage your subscription." },
      },
      getLang: () => "de",
      supportedLanguages: ["ru", "en", "de"],
    });

    i18n.mergeMessages({ de: {} });
    expect(i18n.t("wa_login_intro", {}, "Sign in fallback")).toBe(
      "Sign in to manage your subscription."
    );
    expect(i18n.t("wa_missing_key", {}, "Sign in fallback")).toBe("Sign in fallback");
  });

  it("preserves an explicitly emptied string instead of falling back", () => {
    const i18n = createI18n({
      messages: {
        ru: { wa_login_intro: "" },
        en: { wa_login_intro: "Sign in to manage your subscription." },
      },
      getLang: () => "ru",
      supportedLanguages: ["ru", "en"],
    });

    expect(i18n.t("wa_login_intro", {}, "Sign in fallback")).toBe("");
  });
});
