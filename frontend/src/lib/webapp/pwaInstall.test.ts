import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "svelte/server";
import PwaInstallOffer from "../../webapp/pwa/PwaInstallOffer.svelte";

import {
  PWA_INSTALL_OFFER_STORAGE_KEY,
  getPwaInstallSnapshot,
  hasPwaOfferBeenShown,
  initPwaInstall,
  isPwaStandaloneLaunch,
  isPwaInstallAvailable,
  markPwaOfferDismissed,
  markPwaOfferShown,
  promptPwaInstall,
  reportPwaInstallClicked,
  resetPwaInstall,
  setPwaInstallTelegramContext,
  shouldShowPwaInstallOffer,
} from "./pwaInstall.svelte";
import {
  destroyOnboardingSignals,
  initOnboardingSignals,
  type OnboardingSignal,
  type OnboardingSignalDescriptor,
} from "./onboardingSignals";

const ANDROID_UA =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36";
const IOS_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1";
const DESKTOP_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";
const EMBEDDED_UA =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/124.0.0.0 Mobile Safari/537.36";

type FakeStorage = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
  removeItem: (key: string) => void;
  clear: () => void;
};

type WindowStub = {
  localStorage: FakeStorage;
  sessionStorage: FakeStorage;
  location: { search: string; hash: string; href: string };
  Telegram?: { WebApp: { initData: string } };
  matchMedia: (query: string) => {
    matches: boolean;
    addEventListener: () => void;
    removeEventListener: () => void;
  };
  addEventListener: (type: string, listener: (event: Event) => void) => void;
  removeEventListener: (type: string, listener: (event: Event) => void) => void;
  emit: (type: string, event: Event) => void;
  dataLayer?: unknown[];
};

function fakeStorage(options: { throwGet?: boolean; throwSet?: boolean } = {}): FakeStorage {
  const store = new Map<string, string>();
  return {
    getItem: (key) => {
      if (options.throwGet) throw new Error("storage blocked");
      return store.get(key) ?? null;
    },
    setItem: (key, value) => {
      if (options.throwSet) throw new Error("storage blocked");
      store.set(key, value);
    },
    removeItem: (key) => void store.delete(key),
    clear: () => store.clear(),
  };
}

function installWindow(options: {
  storage?: FakeStorage;
  standalone?: boolean;
  telegram?: boolean;
  search?: string;
}): WindowStub {
  const listeners = new Map<string, Set<(event: Event) => void>>();
  const win: WindowStub = {
    localStorage: options.storage ?? fakeStorage(),
    sessionStorage: fakeStorage(),
    location: {
      search: options.search ?? "",
      hash: "",
      href: "https://shop.example/",
    },
    ...(options.telegram ? { Telegram: { WebApp: { initData: "query_id=1" } } } : {}),
    matchMedia: (query) => ({
      matches: Boolean(options.standalone) && query.includes("standalone"),
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
    addEventListener: (type, listener) => {
      const bucket = listeners.get(type) ?? new Set();
      bucket.add(listener);
      listeners.set(type, bucket);
    },
    removeEventListener: (type, listener) => {
      listeners.get(type)?.delete(listener);
    },
    emit: (type, event) => {
      for (const listener of listeners.get(type) ?? []) listener(event);
    },
  };
  return win;
}

function installDocument(manifest: boolean): void {
  vi.stubGlobal("document", {
    getElementById: (id: string) => (manifest && id === "app-manifest" ? {} : null),
    querySelector: (selector: string) =>
      manifest && selector.includes('link[rel="manifest"]') ? {} : null,
    querySelectorAll: () => [],
    createElement: () => ({
      addEventListener: () => {},
      setAttribute: () => {},
      remove: () => {},
    }),
    head: { appendChild: () => {} },
  });
}

function installNavigator(options: {
  ua?: string;
  installedRelatedApps?: () => Promise<unknown[]>;
}): void {
  vi.stubGlobal("navigator", {
    userAgent: options.ua ?? ANDROID_UA,
    platform: "",
    maxTouchPoints: 0,
    standalone: false,
    getInstalledRelatedApps: options.installedRelatedApps,
  });
}

function setup(
  options: {
    ua?: string;
    manifest?: boolean;
    standalone?: boolean;
    storage?: FakeStorage;
    telegram?: boolean;
    search?: string;
    installedRelatedApps?: () => Promise<unknown[]>;
  } = {}
) {
  const storage = options.storage ?? fakeStorage();
  const win = installWindow({ ...options, storage });
  vi.stubGlobal("window", win);
  installDocument(options.manifest ?? true);
  installNavigator(options);
  return { win, storage };
}

function promptEvent(outcome: "accepted" | "dismissed") {
  const prompt = vi.fn(() => Promise.resolve());
  const event = {
    preventDefault: vi.fn(),
    prompt,
    userChoice: Promise.resolve({ outcome }),
  } as unknown as Event;
  return { event, prompt };
}

function readOffer(storage: FakeStorage): { shownAt: number; outcome: string } | null {
  const raw = storage.getItem(PWA_INSTALL_OFFER_STORAGE_KEY);
  if (!raw) return null;
  const value: unknown = JSON.parse(raw);
  return value as { shownAt: number; outcome: string };
}

function signalDescriptor(): OnboardingSignalDescriptor {
  return {
    owner: "ga4funnel",
    id: "web",
    entry: "",
    scripts: [],
    connect_hosts: ["https://www.google-analytics.com"],
    config: {},
  };
}

beforeEach(() => {
  resetPwaInstall();
});

afterEach(() => {
  resetPwaInstall();
  destroyOnboardingSignals();
  vi.unstubAllGlobals();
});

describe("pwa install state", () => {
  it("captures the deferred prompt without prompting on init or offer render", () => {
    const { win } = setup({ ua: ANDROID_UA });
    initPwaInstall();
    const { event, prompt } = promptEvent("accepted");
    win.emit("beforeinstallprompt", event);
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(prompt).not.toHaveBeenCalled();
    expect(getPwaInstallSnapshot()).toEqual({
      state: "prompt-available",
      platform: "android",
      canPrompt: true,
    });
    markPwaOfferShown();
    hasPwaOfferBeenShown();
    expect(prompt).not.toHaveBeenCalled();
  });

  it("detects installed state from standalone display and iOS navigator flag", () => {
    setup({ ua: DESKTOP_UA, standalone: true });
    initPwaInstall();
    expect(getPwaInstallSnapshot().state).toBe("installed");
    expect(isPwaStandaloneLaunch()).toBe(true);
    expect(isPwaInstallAvailable()).toBe(false);

    resetPwaInstall();
    setup({ ua: IOS_UA, standalone: true });
    initPwaInstall();
    expect(getPwaInstallSnapshot()).toMatchObject({ state: "installed", platform: "ios" });
    expect(isPwaStandaloneLaunch()).toBe(true);
  });

  it("does not confuse an installed-app hint with a standalone launch", () => {
    const { win } = setup({ ua: IOS_UA });
    initPwaInstall();
    win.emit("appinstalled", new Event("appinstalled"));
    expect(getPwaInstallSnapshot()).toMatchObject({ state: "installed", platform: "ios" });
    expect(isPwaStandaloneLaunch()).toBe(false);
  });

  it("uses getInstalledRelatedApps as a best-effort installed hint", async () => {
    setup({
      ua: ANDROID_UA,
      installedRelatedApps: () => Promise.resolve([{ id: "https://shop.example/" }]),
    });
    initPwaInstall();
    await Promise.resolve();
    expect(getPwaInstallSnapshot().state).toBe("installed");
  });

  it("reports guidance states for iOS, Android and desktop without a prompt", () => {
    setup({ ua: IOS_UA });
    initPwaInstall();
    expect(getPwaInstallSnapshot()).toEqual({
      state: "guidance-only",
      platform: "ios",
      canPrompt: false,
    });
    expect(isPwaInstallAvailable()).toBe(true);

    resetPwaInstall();
    setup({ ua: ANDROID_UA });
    initPwaInstall();
    expect(getPwaInstallSnapshot()).toMatchObject({ state: "unknown", platform: "android" });

    resetPwaInstall();
    setup({ ua: DESKTOP_UA });
    initPwaInstall();
    expect(getPwaInstallSnapshot()).toMatchObject({ state: "unknown", platform: "desktop" });

    resetPwaInstall();
    setup({
      ua: "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    });
    initPwaInstall();
    expect(getPwaInstallSnapshot()).toMatchObject({ state: "guidance-only", platform: "desktop" });
  });

  it("suppresses offers in Telegram, embedded WebViews and when PWA is disabled", () => {
    setup({ ua: ANDROID_UA });
    initPwaInstall({ telegramMiniApp: true });
    expect(getPwaInstallSnapshot().state).toBe("embedded");
    expect(isPwaInstallAvailable()).toBe(false);

    resetPwaInstall();
    setup({ ua: EMBEDDED_UA });
    initPwaInstall();
    expect(getPwaInstallSnapshot().state).toBe("embedded");

    resetPwaInstall();
    setup({ ua: ANDROID_UA, manifest: false });
    initPwaInstall();
    expect(getPwaInstallSnapshot().state).toBe("pwa-disabled");
    expect(shouldShowPwaInstallOffer()).toBe(false);
    expect(isPwaInstallAvailable()).toBe(false);
  });

  it("lets the shell mark Telegram context after early initialization", () => {
    setup({ ua: ANDROID_UA });
    initPwaInstall();
    expect(getPwaInstallSnapshot().state).toBe("unknown");
    setPwaInstallTelegramContext(true);
    expect(getPwaInstallSnapshot().state).toBe("embedded");
  });
});

describe("one-time offer storage", () => {
  it("keeps the home card visible after a prior dismissal and on a later visit until installation", async () => {
    const { win } = setup({ ua: ANDROID_UA });
    initPwaInstall();
    markPwaOfferShown();
    markPwaOfferDismissed();
    const props = { persistent: true, t: (key: string) => key };
    expect(render(PwaInstallOffer, { props }).body).toContain("pwa-install-offer-copy");
    resetPwaInstall();
    initPwaInstall();
    expect(render(PwaInstallOffer, { props }).body).toContain("pwa-install-offer-copy");
    const { event } = promptEvent("accepted");
    win.emit("beforeinstallprompt", event);
    await promptPwaInstall();
    expect(render(PwaInstallOffer, { props }).body).toContain("pwa-install-offer-copy");
    win.emit("appinstalled", new Event("appinstalled"));
    expect(render(PwaInstallOffer, { props }).body).not.toContain("pwa-install-offer-copy");
    resetPwaInstall();
    initPwaInstall();
    expect(render(PwaInstallOffer, { props }).body).not.toContain("pwa-install-offer-copy");
    win.emit("beforeinstallprompt", promptEvent("dismissed").event);
    expect(render(PwaInstallOffer, { props }).body).toContain("pwa-install-offer-copy");
  });

  it("marks the offer once per browser/device and never keys it by user", () => {
    const { storage } = setup({ ua: ANDROID_UA });
    initPwaInstall();
    expect(shouldShowPwaInstallOffer()).toBe(true);
    markPwaOfferShown();
    expect(shouldShowPwaInstallOffer()).toBe(false);
    expect(hasPwaOfferBeenShown()).toBe(true);
    const stored = readOffer(storage);
    expect(stored?.outcome).toBe("shown");
    expect(stored?.shownAt).toBeGreaterThan(0);
    expect(stored).not.toHaveProperty("userId");
    expect(stored).not.toHaveProperty("user_id");

    // Re-render, logout, account switch and later sign-ins on this browser
    // reuse the same non-user-scoped record in a fresh session.
    resetPwaInstall();
    expect(hasPwaOfferBeenShown()).toBe(true);
    expect(shouldShowPwaInstallOffer()).toBe(false);
  });

  it("keeps dismissed offers dismissed while the settings action remains", () => {
    const { storage } = setup({ ua: ANDROID_UA });
    initPwaInstall();
    markPwaOfferShown();
    markPwaOfferDismissed();
    expect(readOffer(storage)?.outcome).toBe("dismissed");
    expect(hasPwaOfferBeenShown()).toBe(true);
    expect(shouldShowPwaInstallOffer()).toBe(false);
    expect(isPwaInstallAvailable(getPwaInstallSnapshot().state)).toBe(true);
  });

  it("treats malformed or cleared storage as not shown and allows a fresh offer", () => {
    const storage = fakeStorage();
    setup({ ua: ANDROID_UA, storage });
    storage.setItem(PWA_INSTALL_OFFER_STORAGE_KEY, "{not-json");
    initPwaInstall();
    expect(hasPwaOfferBeenShown()).toBe(false);
    expect(shouldShowPwaInstallOffer()).toBe(true);

    markPwaOfferShown();
    expect(hasPwaOfferBeenShown()).toBe(true);
    storage.removeItem(PWA_INSTALL_OFFER_STORAGE_KEY);
    expect(hasPwaOfferBeenShown()).toBe(false);
    expect(shouldShowPwaInstallOffer()).toBe(true);
  });

  it("does not loop the offer when storage throws", () => {
    setup({ ua: ANDROID_UA, storage: fakeStorage({ throwGet: true, throwSet: true }) });
    initPwaInstall();
    expect(hasPwaOfferBeenShown()).toBe(false);
    expect(shouldShowPwaInstallOffer()).toBe(true);
    markPwaOfferShown();
    expect(hasPwaOfferBeenShown()).toBe(true);
    expect(shouldShowPwaInstallOffer()).toBe(false);
  });

  it("remembers a successful show when storage becomes inaccessible on re-render/sign-in", () => {
    const storageOptions = { throwGet: false, throwSet: false };
    setup({ ua: ANDROID_UA, storage: fakeStorage(storageOptions) });
    initPwaInstall();
    expect(shouldShowPwaInstallOffer()).toBe(true);
    markPwaOfferShown();
    expect(shouldShowPwaInstallOffer()).toBe(false);

    // Storage can become blocked later (private mode, revoked permission or
    // an unavailable iframe). Navigating away/back or signing in again must
    // still consult the in-memory show record instead of repeating the offer.
    storageOptions.throwGet = true;
    storageOptions.throwSet = true;
    initPwaInstall();
    expect(hasPwaOfferBeenShown()).toBe(true);
    expect(shouldShowPwaInstallOffer()).toBe(false);
  });
});

describe("explicit install prompt", () => {
  it("reports accepted and leaves installed state to appinstalled", async () => {
    const { win, storage } = setup({ ua: ANDROID_UA });
    initPwaInstall();
    markPwaOfferShown();
    const { event, prompt } = promptEvent("accepted");
    win.emit("beforeinstallprompt", event);
    await expect(promptPwaInstall()).resolves.toBe("accepted");
    expect(prompt).toHaveBeenCalledTimes(1);
    expect(getPwaInstallSnapshot().state).not.toBe("installed");
    expect(readOffer(storage)?.outcome).toBe("accepted");

    win.emit("appinstalled", new Event("appinstalled"));
    expect(getPwaInstallSnapshot().state).toBe("installed");
    expect(readOffer(storage)?.outcome).toBe("installed");
  });

  it("reports dismissed and unavailable outcomes", async () => {
    const { win, storage } = setup({ ua: ANDROID_UA });
    initPwaInstall();
    markPwaOfferShown();
    const { event } = promptEvent("dismissed");
    win.emit("beforeinstallprompt", event);
    await expect(promptPwaInstall()).resolves.toBe("dismissed");
    expect(readOffer(storage)?.outcome).toBe("dismissed");

    resetPwaInstall();
    const rejected = setup({ ua: ANDROID_UA });
    initPwaInstall();
    const failing = promptEvent("accepted");
    failing.event = {
      preventDefault: vi.fn(),
      prompt: () => Promise.reject(new Error("prompt failed")),
      userChoice: Promise.resolve({ outcome: "accepted" }),
    } as unknown as Event;
    rejected.win.emit("beforeinstallprompt", failing.event);
    await expect(promptPwaInstall()).resolves.toBe("unavailable");

    resetPwaInstall();
    setup({ ua: ANDROID_UA });
    initPwaInstall();
    await expect(promptPwaInstall()).resolves.toBe("unavailable");
  });

  it("does not prompt when the app is already installed or embedded", async () => {
    setup({ ua: DESKTOP_UA, standalone: true });
    initPwaInstall();
    await expect(promptPwaInstall()).resolves.toBe("unavailable");
    expect(getPwaInstallSnapshot().canPrompt).toBe(false);

    resetPwaInstall();
    setup({ ua: IOS_UA });
    initPwaInstall();
    await expect(promptPwaInstall()).resolves.toBe("unavailable");
    expect(getPwaInstallSnapshot().canPrompt).toBe(false);
  });
});

describe("pwa analytics signals", () => {
  it("emits only allowlisted PWA actions and never URLs or tokens", async () => {
    const { win } = setup({ ua: ANDROID_UA });
    initOnboardingSignals([signalDescriptor()], { code: "campaign", utm: {} });
    initPwaInstall();
    const { event } = promptEvent("accepted");
    win.emit("beforeinstallprompt", event);
    reportPwaInstallClicked("settings");
    await promptPwaInstall();
    win.emit("appinstalled", new Event("appinstalled"));

    const layer = win.dataLayer as OnboardingSignal[];
    expect(layer.map((signal) => signal.action)).toEqual([
      "pwa_install_clicked",
      "pwa_install_prompt_accepted",
      "pwa_app_installed",
    ]);
    expect(layer.map((signal) => signal.screen)).toEqual(["settings", "settings", "settings"]);
    for (const signal of layer) {
      expect(signal.surface).toBe("app");
      expect(JSON.stringify(signal)).not.toMatch(/https?:|token|s\//i);
    }
  });

  it("emits nothing when onboarding analytics are disabled", async () => {
    const { win } = setup({ ua: ANDROID_UA });
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    initOnboardingSignals([], { code: "campaign", utm: {} });
    initPwaInstall();
    const { event } = promptEvent("dismissed");
    win.emit("beforeinstallprompt", event);
    reportPwaInstallClicked("home");
    await promptPwaInstall();
    win.emit("appinstalled", new Event("appinstalled"));
    expect(win.dataLayer).toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
