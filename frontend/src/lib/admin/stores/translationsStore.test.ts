import { QueryClient } from "@tanstack/svelte-query";
import { describe, expect, it, vi } from "vitest";

import { createTranslationsStore } from "./translationsStore.svelte.js";
import type { TranslationItem } from "./translationsStore.svelte.js";

const INTRO_BASE = {
  ru: "Войдите, чтобы управлять подпиской.",
  en: "Sign in to manage your subscription.",
} as const;

type IntroDb = Record<string, Record<string, string>>;
type PatchBody = {
  updates: Record<string, Record<string, string>>;
  deletes: Array<{ lang: string; key: string }>;
};

function introGroups(db: IntroDb) {
  return [
    {
      id: "webapp",
      title: "Mini App",
      audience: "user",
      items: [
        {
          key: "wa_login_intro",
          audience: "user",
          values: Object.fromEntries(
            (Object.keys(INTRO_BASE) as Array<keyof typeof INTRO_BASE>).map((lang) => {
              const override = db[lang]?.["wa_login_intro"];
              const overridden = override !== undefined;
              return [
                lang,
                {
                  base: INTRO_BASE[lang],
                  fallback: INTRO_BASE[lang],
                  effective: overridden ? override : INTRO_BASE[lang],
                  override: overridden ? override : "",
                  overridden,
                },
              ];
            })
          ),
        },
      ],
    },
  ];
}

function setupIntroStore(initial: IntroDb = {}) {
  const db: IntroDb = JSON.parse(JSON.stringify(initial));
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const patchBodies: PatchBody[] = [];
  const api = vi.fn(async (_path: unknown, options?: { method?: string; body?: unknown }) => {
    if (options?.method === "PATCH") {
      const body = JSON.parse(String(options.body)) as PatchBody;
      patchBodies.push(body);
      for (const [lang, messages] of Object.entries(body.updates ?? {})) {
        db[lang] = { ...(db[lang] ?? {}), ...messages };
      }
      for (const item of body.deletes ?? []) {
        delete db[item.lang]?.[item.key];
        if (db[item.lang] && !Object.keys(db[item.lang]).length) delete db[item.lang];
      }
      return { ok: true, applied: 1, reverted: 0, file_written: true };
    }
    return {
      ok: true,
      languages: [
        { code: "ru", label: "Russian", flag: "🇷🇺", base: true },
        { code: "en", label: "English", flag: "🇬🇧", base: false },
      ],
      groups: introGroups(db),
      path: "data/locales-overrides.json",
      override_count: 0,
    };
  });
  const store = createTranslationsStore({
    api: api as never,
    at: (_key, _params, fallback) => fallback || "",
    onToast: vi.fn(),
    queryClient,
  });
  return { store, patchBodies, queryClient };
}

function introItem(store: ReturnType<typeof setupIntroStore>["store"]): TranslationItem {
  const item = store.translationGroups
    .flatMap((group) => group.items ?? [])
    .find((entry) => entry.key === "wa_login_intro");
  if (!item) throw new Error("wa_login_intro missing from translations payload");
  return item;
}

describe("translationsStore", () => {
  it("reuses the large translations query and refreshes it explicitly", async () => {
    const api = vi.fn().mockResolvedValue({
      ok: true,
      languages: [{ code: "en", label: "English", flag: "🇬🇧", base: true }],
      groups: [],
      path: "locales-overrides.json",
      override_count: 0,
    });
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const store = createTranslationsStore({
      api: api as never,
      at: (_key, _params, fallback) => fallback || "",
      onToast: vi.fn(),
      queryClient,
    });

    await store.loadTranslations();
    await store.loadTranslations();
    expect(api).toHaveBeenCalledTimes(1);

    await store.loadTranslations({ refresh: true });
    expect(api).toHaveBeenCalledTimes(2);
    queryClient.clear();
  });

  it("saves an explicitly emptied intro as an update so reload stays hidden", async () => {
    const { store, patchBodies, queryClient } = setupIntroStore();
    await store.loadTranslations();

    store.markDirty("ru", "wa_login_intro", "");
    const saved: unknown[] = [];
    await store.saveTranslations((payload) => {
      saved.push(payload);
    });

    expect(patchBodies).toEqual([{ updates: { ru: { wa_login_intro: "" } }, deletes: [] }]);
    expect(saved).toEqual([{ updates: { ru: { wa_login_intro: "" } }, deletes: [] }]);

    const item = introItem(store);
    expect(item.values?.["ru"]?.override).toBe("");
    expect(item.values?.["ru"]?.overridden).toBe(true);
    expect(item.values?.["ru"]?.effective).toBe("");
    // The untouched language keeps serving its base copy.
    expect(item.values?.["en"]?.overridden).toBe(false);
    expect(item.values?.["en"]?.effective).toBe(INTRO_BASE.en);
    queryClient.clear();
  });

  it("keeps empty overrides isolated per language", async () => {
    const { store, patchBodies, queryClient } = setupIntroStore();
    await store.loadTranslations();

    store.markDirty("ru", "wa_login_intro", "");
    store.markDirty("en", "wa_login_intro", "Welcome back!");
    await store.saveTranslations();

    expect(patchBodies).toEqual([
      {
        updates: { ru: { wa_login_intro: "" }, en: { wa_login_intro: "Welcome back!" } },
        deletes: [],
      },
    ]);

    const item = introItem(store);
    expect(item.values?.["ru"]?.override).toBe("");
    expect(item.values?.["ru"]?.overridden).toBe(true);
    expect(item.values?.["en"]?.override).toBe("Welcome back!");
    expect(item.values?.["en"]?.overridden).toBe(true);
    queryClient.clear();
  });

  it("reverts an empty override back to base only through Reset deletes", async () => {
    const { store, patchBodies, queryClient } = setupIntroStore({ ru: { wa_login_intro: "" } });
    await store.loadTranslations();
    expect(introItem(store).values?.["ru"]?.overridden).toBe(true);

    store.resetField("ru", "wa_login_intro", true);
    await store.saveTranslations();

    expect(patchBodies).toEqual([
      { updates: {}, deletes: [{ lang: "ru", key: "wa_login_intro" }] },
    ]);

    const item = introItem(store);
    expect(item.values?.["ru"]?.overridden).toBe(false);
    expect(item.values?.["ru"]?.effective).toBe(INTRO_BASE.ru);
    // The other language is unaffected by the revert.
    expect(item.values?.["en"]?.overridden).toBe(false);
    expect(item.values?.["en"]?.effective).toBe(INTRO_BASE.en);
    queryClient.clear();
  });
});
