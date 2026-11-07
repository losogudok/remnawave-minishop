import { afterEach, describe, expect, it, vi } from "vitest";
import {
  classifyInstallOpenTarget,
  destroyOnboardingSignals,
  initOnboardingSignals,
  parseOnboardingSignalDescriptors,
  reportInstallInteraction,
  reportOnboardingAction,
  reportOnboardingScreen,
  resetOnboardingAuth,
  resolveInstallSignalKind,
  sanitizeOnboardingAttribution,
  setOnboardingAuthenticated,
  subscribeOnboardingSignals,
  toOnboardingAction,
  toOnboardingScreen,
  withOnboardingSignalActions,
  type OnboardingSignal,
} from "./onboardingSignals";
import { createExtensionHost } from "./extensionHost";
import { createApiClient } from "./publicApi";

function stubWindow() {
  const win: Record<string, unknown> = {};
  vi.stubGlobal("window", win);
  return win;
}

function stubDocument(appended: { src: string }[]) {
  vi.stubGlobal("document", {
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: () => {
      const element: Record<string, unknown> & { remove: () => void } = {
        listeners: {} as Record<string, () => void>,
        remove: () => {},
        addEventListener: (name: string, handler: () => void) => {
          (element.listeners as Record<string, () => void>)[name] = handler;
        },
        setAttribute: () => {},
      };
      return new Proxy(element, {
        set(target, property, value) {
          if (property === "src" && typeof value === "string") appended.push({ src: value });
          return Reflect.set(target, property, value);
        },
      });
    },
    head: { appendChild: () => {} },
  });
}

function validDescriptor() {
  return {
    owner: "ga4funnel",
    id: "web",
    entry: "",
    scripts: ["https://www.googletagmanager.com/gtag/js?id=G-123"],
    connect_hosts: ["https://www.google-analytics.com"],
    config: { measurement_id: "G-123", enabled: true },
  };
}

afterEach(() => {
  destroyOnboardingSignals();
  vi.unstubAllGlobals();
});

describe("onboarding signal descriptors", () => {
  it("accepts a valid descriptor and drops invalid entries", () => {
    expect(parseOnboardingSignalDescriptors([validDescriptor()])).toHaveLength(1);
    expect(parseOnboardingSignalDescriptors("nope")).toEqual([]);
    expect(
      parseOnboardingSignalDescriptors([
        { ...validDescriptor(), scripts: ["http://insecure.example/x.js"] },
        { ...validDescriptor(), owner: "Bad Owner!" },
        { ...validDescriptor(), entry: "https://evil.example/x.js" },
        { ...validDescriptor(), config: { nested: { deep: 1 } } },
        { ...validDescriptor(), config: { token: "x".repeat(300) } },
      ])
    ).toEqual([]);
  });

  it("drops duplicates and caps the integration count", () => {
    const many = Array.from({ length: 6 }, (_, index) => ({
      ...validDescriptor(),
      id: `web${index}`,
    }));
    expect(parseOnboardingSignalDescriptors(many)).toHaveLength(4);
    expect(parseOnboardingSignalDescriptors([validDescriptor(), validDescriptor()])).toHaveLength(
      1
    );
  });
});

describe("onboarding attribution sanitization", () => {
  it("keeps only campaign code and known utm keys", () => {
    expect(
      sanitizeOnboardingAttribution({
        code: "summer_sale",
        utm: { utm_source: "tg", utm_medium: "cpc", evil: "x".repeat(10) },
      })
    ).toEqual({ code: "summer_sale", utm: { utm_source: "tg", utm_medium: "cpc" } });
  });

  it("rejects raw urls, tokens, emails and oversized values", () => {
    expect(sanitizeOnboardingAttribution({ code: "s/abc123token" }).code).toBe("");
    expect(sanitizeOnboardingAttribution({ code: "user@example.com" }).code).toBe("");
    expect(
      sanitizeOnboardingAttribution({ code: "ok", utm: { utm_source: "x".repeat(300) } }).utm
    ).toEqual({});
    expect(sanitizeOnboardingAttribution(null)).toEqual({ code: "", utm: {} });
  });

  it("drops email-like and token-url utm values while keeping safe patterns", () => {
    const sanitized = sanitizeOnboardingAttribution({
      code: "summer_sale",
      utm: {
        utm_source: "user@example.com",
        utm_medium: "https://example.com/s/token123",
        utm_campaign: "summer_sale-2024",
        utm_content: "tg",
        utm_term: "a".repeat(200),
      },
    });
    expect(sanitized.utm.utm_source).toBeUndefined();
    expect(sanitized.utm.utm_medium).toBeUndefined();
    expect(sanitized.utm.utm_term).toBeUndefined();
    expect(sanitized.utm.utm_campaign).toBe("summer_sale-2024");
    expect(sanitized.utm.utm_content).toBe("tg");
    expect(JSON.stringify(sanitized)).not.toContain("user@example.com");
    expect(JSON.stringify(sanitized)).not.toContain("token123");
  });
});

describe("onboarding screen and action names", () => {
  it("maps shell mode and screen to safe names", () => {
    expect(toOnboardingScreen("login", "anything")).toEqual({ screen: "login", surface: "guest" });
    expect(toOnboardingScreen("publicInstall", "x")).toEqual({
      screen: "public_install",
      surface: "public",
    });
    expect(toOnboardingScreen("app", "payment-history")).toEqual({
      screen: "payment_history",
      surface: "app",
    });
    expect(toOnboardingScreen("app", "extensions")).toEqual({
      screen: "extensions",
      surface: "app",
    });
    expect(toOnboardingScreen("app", "checkout?token=secret")).toBeNull();
    expect(toOnboardingScreen("loading", "home")).toBeNull();
  });

  it("allowlist actions and drops unknown names", () => {
    expect(toOnboardingAction("connect_opened")).toBe("connect_opened");
    expect(toOnboardingAction("https://evil.example")).toBeNull();
    expect(toOnboardingAction("user.registered")).toBeNull();
  });
});

describe("onboarding signal lifecycle", () => {
  it("emits screens once per surface and never duplicates on repeated navigation", () => {
    const win = stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], { code: "summer", utm: {} });
    reportOnboardingScreen("login", "guest");
    reportOnboardingScreen("login", "guest");
    reportOnboardingScreen("public_install", "public");
    reportOnboardingScreen("install", "app");
    reportOnboardingScreen("install", "app");
    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer.map((signal) => `${signal.surface}:${signal.screen}`)).toEqual([
      "guest:login",
      "public:public_install",
      "app:install",
    ]);
    expect(layer[0]).toMatchObject({
      event: "minishop_screen_viewed",
      action: "screen_viewed",
      ad_code: "summer",
    });
  });

  it("reports auth and trial milestones once across guest-to-auth transitions", () => {
    const win = stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    setOnboardingAuthenticated();
    reportOnboardingAction("auth_completed", "home", "app");
    reportOnboardingAction("auth_completed", "home", "app");
    reportOnboardingAction("trial_activated", "trial", "app");
    reportOnboardingAction("trial_activated", "trial", "app");
    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer.map((signal) => signal.event)).toEqual([
      "minishop_auth_completed",
      "minishop_trial_activated",
    ]);
  });

  it("does nothing when disabled: no scripts, no data layer, no fetch", () => {
    const win = stubWindow();
    const appended: { src: string }[] = [];
    stubDocument(appended);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    initOnboardingSignals([], { code: "summer", utm: {} });
    reportOnboardingScreen("login", "guest");
    reportOnboardingAction("trial_activated", "trial", "app");
    setOnboardingAuthenticated();
    expect(appended).toEqual([]);
    expect(win.dataLayer).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("isolates provider script failures without blocking signals", () => {
    const win = stubWindow();
    const elements: { handlers: Record<string, () => void>; removed: boolean }[] = [];
    vi.stubGlobal("document", {
      querySelector: () => null,
      querySelectorAll: () => [],
      createElement: () => {
        const record: { handlers: Record<string, () => void>; removed: boolean } = {
          handlers: {},
          removed: false,
        };
        elements.push(record);
        return {
          set src(_value: string) {},
          set async(_value: boolean) {},
          setAttribute: () => {},
          addEventListener: (name: string, handler: () => void) => {
            record.handlers[name] = handler;
          },
          remove: () => {
            record.removed = true;
          },
        };
      },
      head: { appendChild: () => {} },
    });
    initOnboardingSignals([validDescriptor()], null);
    expect(elements).toHaveLength(1);
    elements[0].handlers.error();
    expect(elements[0].removed).toBe(true);
    reportOnboardingScreen("login", "guest");
    expect((win.dataLayer as OnboardingSignal[]).map((signal) => signal.event)).toEqual([
      "minishop_screen_viewed",
    ]);
  });

  it("isolates adapter entry failures and replays buffered visit on subscribe", async () => {
    stubWindow();
    stubDocument([]);
    initOnboardingSignals(
      [{ ...validDescriptor(), scripts: [], entry: "/api/plugins/ga4funnel/signals.js" }],
      null
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    reportOnboardingScreen("login", "guest");
    const seen: OnboardingSignal[] = [];
    const unsubscribe = subscribeOnboardingSignals((signal) => seen.push(signal));
    expect(seen.map((signal) => signal.screen)).toEqual(["login"]);
    reportOnboardingScreen("install", "app");
    unsubscribe();
    expect(seen.map((signal) => signal.screen)).toEqual(["login", "install"]);
  });

  it("buffers initial visit for late adapters without duplicating views", () => {
    stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], { code: "summer", utm: {} });
    reportOnboardingScreen("install", "app");
    reportOnboardingScreen("install", "app");
    const first: OnboardingSignal[] = [];
    const unsubFirst = subscribeOnboardingSignals((signal) => first.push(signal));
    expect(first.filter((s) => s.action === "screen_viewed")).toHaveLength(1);
    const second: OnboardingSignal[] = [];
    const unsubSecond = subscribeOnboardingSignals((signal) => second.push(signal));
    expect(second.filter((s) => s.action === "screen_viewed")).toHaveLength(1);
    unsubFirst();
    unsubSecond();
  });
});

describe("onboarding signal action wrapping", () => {
  it("reports sanitized actions and always runs the product action", async () => {
    const win = stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], { code: "promo", utm: {} });
    const calls: string[] = [];
    const wrapped = withOnboardingSignalActions(
      {
        openConnectLink: () => {
          calls.push("connect");
          return true;
        },
        openPublicConnectLink: () => false,
        openTrialConnectLink: () => true,
        openActivationConnectLink: () => true,
        openAppLink: (_url: string) => {
          calls.push("app");
        },
        openExternalLink: (_url: string) => {
          calls.push("external");
        },
        copyText: async (_text: string, _message?: string) => {
          calls.push("copy");
        },
      },
      () => ({ screen: "home", surface: "app" })
    );
    expect(wrapped.openConnectLink()).toBe(true);
    expect(wrapped.openPublicConnectLink()).toBe(false);
    wrapped.openTrialConnectLink();
    wrapped.openAppLink("vless://profile-123");
    wrapped.openExternalLink("https://example.com/client");
    await wrapped.copyText("secret-subscription-link", "Copied");
    expect(calls).toEqual(["connect", "app", "external", "copy"]);
    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer.map((signal) => signal.action)).toEqual([
      "connect_opened",
      "connect_opened",
      "app_opened",
      "external_opened",
      "text_copied",
    ]);
    for (const signal of layer) {
      expect(JSON.stringify(signal)).not.toContain("secret-subscription-link");
      expect(JSON.stringify(signal)).not.toContain("example.com");
    }
  });

  it("distinguishes download vs import by URL scheme with safe fields only", () => {
    const win = stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    const wrapped = withOnboardingSignalActions(
      {
        openConnectLink: () => true,
        openPublicConnectLink: () => false,
        openTrialConnectLink: () => false,
        openActivationConnectLink: () => false,
        openAppLink: (_url: string) => {},
        openExternalLink: (_url: string) => {},
        copyText: async () => {},
      },
      () => ({ screen: "home", surface: "app" })
    );
    wrapped.openAppLink("https://cdn.example.com/app.apk");
    wrapped.openAppLink("vless://import-profile");
    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer.map((signal) => signal.action)).toEqual(["external_opened", "app_opened"]);
    expect(classifyInstallOpenTarget("https://cdn.example.com/app.apk")).toBe("download");
    expect(classifyInstallOpenTarget("vless://import-profile")).toBe("import");
    for (const signal of layer) {
      expect(JSON.stringify(signal)).not.toContain("cdn.example.com");
      expect(JSON.stringify(signal)).not.toContain("vless");
    }
  });

  it("emits explicit install semantics and silences generic wrapper on install", async () => {
    const win = stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    const wrapped = withOnboardingSignalActions(
      {
        openConnectLink: () => true,
        openPublicConnectLink: () => false,
        openTrialConnectLink: () => false,
        openActivationConnectLink: () => false,
        openAppLink: (_url: string) => {},
        openExternalLink: (_url: string) => {},
        copyText: async (_text: string, _message?: string) => {},
      },
      () => ({ screen: "install", surface: "app" })
    );
    wrapped.openAppLink("https://cdn.example.com/app.apk");
    wrapped.openExternalLink("https://cdn.example.com/app.apk");
    await wrapped.copyText("secret", "Copied");
    expect(win.dataLayer).toBeUndefined();
    reportInstallInteraction("download", "install", "app");
    reportInstallInteraction("import", "install", "app");
    reportInstallInteraction("copy", "install", "app");
    reportInstallInteraction("share", "install", "app");
    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer.map((signal) => signal.action)).toEqual([
      "external_opened",
      "app_opened",
      "text_copied",
      "share_opened",
    ]);
    for (const signal of layer) {
      expect(JSON.stringify(signal)).not.toContain("secret");
    }
  });

  it("resets auth on logout so the next account re-emits milestones", () => {
    const win = stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    setOnboardingAuthenticated();
    reportOnboardingAction("auth_completed", "home", "app");
    reportOnboardingAction("trial_activated", "trial", "app");
    resetOnboardingAuth();
    setOnboardingAuthenticated();
    reportOnboardingAction("auth_completed", "home", "app");
    reportOnboardingAction("trial_activated", "trial", "app");
    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer.map((signal) => signal.event)).toEqual([
      "minishop_auth_completed",
      "minishop_trial_activated",
      "minishop_auth_completed",
      "minishop_trial_activated",
    ]);
  });

  it("never blocks the product action when reporting fails", async () => {
    stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    let ran = false;
    const wrapped = withOnboardingSignalActions(
      {
        openConnectLink: () => {
          ran = true;
          return true;
        },
        openPublicConnectLink: () => false,
        openTrialConnectLink: () => false,
        openActivationConnectLink: () => false,
        openAppLink: (_url: string) => {},
        openExternalLink: (_url: string) => {
          throw new Error("navigation failed");
        },
        copyText: async (_text: string, _message?: string) => {},
      },
      () => {
        throw new Error("context failed");
      }
    );
    wrapped.openConnectLink();
    expect(ran).toBe(true);
    expect(() => wrapped.openExternalLink("https://example.com")).toThrow("navigation failed");
  });
});

describe("extension host signals", () => {
  it("reports allowlisted plugin actions to the extensions section only", () => {
    const win = stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    const host = createExtensionHost(createApiClient(), "sample", (key) => key);
    host.signals.report("external_opened");
    host.signals.report("trial_activated");
    host.signals.report("auth_completed");
    host.signals.report("screen_viewed");
    host.signals.report("https://evil.example");
    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer).toHaveLength(1);
    expect(layer[0]).toMatchObject({
      event: "minishop_external_opened",
      screen: "extensions",
      surface: "app",
    });
  });
});

describe("onboarding PII sanitization hardening (ONB-04 round 2)", () => {
  it("drops email, numeric Telegram IDs and bare opaque tokens, keeps safe slugs", () => {
    const sanitized = sanitizeOnboardingAttribution({
      code: "summer_sale",
      utm: {
        utm_source: "user@example.com",
        utm_medium: "123456789",
        utm_campaign: "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vWxYz12",
        utm_content: "summer_sale",
        utm_term: "x".repeat(40),
      },
    });
    expect(sanitized.code).toBe("summer_sale");
    expect(sanitized.utm.utm_source).toBeUndefined();
    expect(sanitized.utm.utm_medium).toBeUndefined();
    expect(sanitized.utm.utm_campaign).toBeUndefined();
    expect(sanitized.utm.utm_term).toBeUndefined();
    expect(sanitized.utm.utm_content).toBe("summer_sale");
    expect(JSON.stringify(sanitized)).not.toContain("user@example.com");
    expect(JSON.stringify(sanitized)).not.toContain("123456789");
  });

  it("drops numeric and token-like campaign codes while keeping safe codes", () => {
    expect(sanitizeOnboardingAttribution({ code: "123456789", utm: {} }).code).toBe("");
    expect(
      sanitizeOnboardingAttribution({ code: "aB3dE5fG7hJ9kL2mN4pQ6rS8tU0vWxYz12", utm: {} }).code
    ).toBe("");
    expect(sanitizeOnboardingAttribution({ code: "summer_sale", utm: {} }).code).toBe(
      "summer_sale"
    );
    expect(sanitizeOnboardingAttribution({ code: "summer_sale-2024", utm: {} }).code).toBe(
      "summer_sale-2024"
    );
  });

  it("drops 22-24 char base64url opaque tokens with _ regardless of digits", () => {
    expect(sanitizeOnboardingAttribution({ code: "wMqa_ksnJrPxLvtYzHcBeFUG", utm: {} }).code).toBe(
      ""
    );
    expect(
      sanitizeOnboardingAttribution({
        code: "ok",
        utm: { utm_campaign: "wMqa_ksnJrPxLvtYzHcBeFUG" },
      }).utm.utm_campaign
    ).toBeUndefined();
    expect(sanitizeOnboardingAttribution({ code: "Ab3dE5fG7hJ9kL2mN4pQ_r", utm: {} }).code).toBe(
      ""
    );
    expect(
      sanitizeOnboardingAttribution({
        code: "ok",
        utm: { utm_campaign: "Ab3dE5fG7hJ9kL2mN4pQ_r" },
      }).utm.utm_campaign
    ).toBeUndefined();
    expect(sanitizeOnboardingAttribution({ code: "summer_sale", utm: {} }).code).toBe(
      "summer_sale"
    );
    expect(sanitizeOnboardingAttribution({ code: "summer_sale-2024", utm: {} }).code).toBe(
      "summer_sale-2024"
    );
    expect(
      sanitizeOnboardingAttribution({ code: "ok", utm: { utm_source: "actual-source" } }).utm
        .utm_source
    ).toBe("actual-source");
  });
});

describe("onboarding install semantics (ONB-04 round 2)", () => {
  it("resolves explicit button semantics regardless of URL scheme", () => {
    // HTTPS primary-subscription resource is an import, not a download.
    expect(
      resolveInstallSignalKind({
        buttonType: "subscriptionLink",
        target: { kind: "resource", resourceId: "primary-subscription", representation: "http" },
        url: "https://shop.test/s/token123",
      })
    ).toBe("import");
    // market:// external button is a download, not an import.
    expect(
      resolveInstallSignalKind({
        buttonType: "external",
        target: { kind: "literal" },
        url: "market://details?id=com.example.app",
      })
    ).toBe("download");
    // Cross-check both schemes with both explicit kinds.
    expect(
      resolveInstallSignalKind({
        buttonType: "subscriptionLink",
        target: { kind: "resource", resourceId: "primary-subscription" },
        url: "market://details?id=com.example.app",
      })
    ).toBe("import");
    expect(
      resolveInstallSignalKind({
        buttonType: "external",
        target: { kind: "literal" },
        url: "https://cdn.example.com/app.apk",
      })
    ).toBe("download");
    // Scheme stays a fallback only when no semantic info is available.
    expect(resolveInstallSignalKind({ url: "https://cdn.example.com/app.apk" })).toBe("download");
    expect(resolveInstallSignalKind({ url: "vless://import-profile" })).toBe("import");
    expect(classifyInstallOpenTarget("https://cdn.example.com/app.apk")).toBe("download");
  });

  it("reports explicit install kinds without leaking URLs", () => {
    const win = stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    reportInstallInteraction("import", "install", "app");
    reportInstallInteraction("download", "install", "app");
    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer.map((signal) => signal.action)).toEqual(["app_opened", "external_opened"]);
    for (const signal of layer) {
      expect(JSON.stringify(signal)).not.toContain("cdn.example.com");
      expect(JSON.stringify(signal)).not.toContain("market");
    }
  });
});

describe("onboarding cleanup epoch fence (ONB-04 round 2)", () => {
  it("stale onSignal from before destroy/reinit receives nothing", async () => {
    stubWindow();
    stubDocument([]);
    initOnboardingSignals(
      [{ ...validDescriptor(), scripts: [], entry: "/api/plugins/ga4funnel/signals.js" }],
      null
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    reportOnboardingScreen("login", "guest");
    // Keep a subscription alive across destroy/reinit: its captured epoch
    // must fence both replay and live delivery from the replacement
    // lifecycle.
    const staleSeen: OnboardingSignal[] = [];
    const staleUnsub = subscribeOnboardingSignals((signal) => staleSeen.push(signal));
    expect(staleSeen.map((signal) => signal.screen)).toEqual(["login"]);
    destroyOnboardingSignals();
    // Reinit as the replacement lifecycle with a new view.
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    reportOnboardingScreen("install", "app");
    // Stale listener gets nothing new from the replacement lifecycle.
    expect(staleSeen.map((signal) => signal.screen)).toEqual(["login"]);
    const freshSeen: OnboardingSignal[] = [];
    const freshUnsub = subscribeOnboardingSignals((signal) => freshSeen.push(signal));
    expect(freshSeen.map((signal) => signal.screen)).toEqual(["install"]);
    reportOnboardingScreen("home", "app");
    expect(freshSeen.map((signal) => signal.screen)).toEqual(["install", "home"]);
    // Stale still gets nothing even after further live signals.
    expect(staleSeen.map((signal) => signal.screen)).toEqual(["login"]);
    staleUnsub();
    freshUnsub();
  });
});

describe("onboarding logout trial isolation (ONB-04 round 2)", () => {
  it("drops previous trial/auth history on reset so guest replay leaks nothing", () => {
    stubWindow();
    stubDocument([]);
    initOnboardingSignals([validDescriptor()], null);
    setOnboardingAuthenticated();
    reportOnboardingAction("auth_completed", "home", "app");
    reportOnboardingAction("trial_activated", "trial", "app");
    resetOnboardingAuth();
    // A new subscriber after logout (guest) must not replay the previous
    // account's milestones.
    const guestSeen: OnboardingSignal[] = [];
    const unsubGuest = subscribeOnboardingSignals((signal) => guestSeen.push(signal));
    expect(guestSeen.filter((signal) => signal.action === "trial_activated")).toEqual([]);
    expect(guestSeen.filter((signal) => signal.action === "auth_completed")).toEqual([]);
    unsubGuest();
    // The next account's actual activation still reports anew.
    const win = window as unknown as { dataLayer: OnboardingSignal[] };
    const before = (win.dataLayer as OnboardingSignal[]).length;
    setOnboardingAuthenticated();
    reportOnboardingAction("auth_completed", "home", "app");
    reportOnboardingAction("trial_activated", "trial", "app");
    expect((win.dataLayer as OnboardingSignal[]).length).toBe(before + 2);
  });
});
