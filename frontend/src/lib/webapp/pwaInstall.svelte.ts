import { reportOnboardingAction } from "./onboardingSignals";

/**
 * Platform-aware install state for the standalone Web App (PWA).
 *
 * The browser install prompt (`beforeinstallprompt`) is a one-shot event and
 * may fire before Svelte mounts, so `initPwaInstall()` must run from the entry
 * module. The module only stores the deferred event here; `prompt()` is called
 * exclusively from an explicit user gesture via `promptPwaInstall()`.
 */

export type PwaInstallState =
  "installed" | "prompt-available" | "guidance-only" | "embedded" | "pwa-disabled" | "unknown";

export type PwaInstallPlatform = "ios" | "android" | "desktop" | "other";

export type PwaInstallSnapshot = {
  state: PwaInstallState;
  platform: PwaInstallPlatform;
  canPrompt: boolean;
};

export type PwaOfferOutcome = "shown" | "dismissed" | "accepted" | "installed";

export const PWA_INSTALL_OFFER_STORAGE_KEY = "remnawave:pwa-install-offer:v1";

type PwaInstallSignalScreen = "home" | "settings";

type PwaInstallSignalAction =
  | "pwa_install_clicked"
  | "pwa_install_prompt_accepted"
  | "pwa_install_prompt_dismissed"
  | "pwa_app_installed"
  | "pwa_install_unconfirmed";

type BeforeInstallPromptEvent = Event & {
  prompt?: () => Promise<void> | void;
  userChoice?: Promise<{ outcome?: string }>;
};

type OfferRecord = {
  shownAt: number;
  outcome: PwaOfferOutcome;
};

const OFFER_OUTCOMES: ReadonlySet<string> = new Set([
  "shown",
  "dismissed",
  "accepted",
  "installed",
]);

const EMBEDDED_UA_PATTERN =
  /(Telegram|FBAN|FBAV|FB_IAB|Instagram|Line\/|WhatsApp|Twitter|; wv\)|WebView|Electron)/i;

const TELEGRAM_LAUNCH_KEYS = [
  "tgWebAppData",
  "tgWebAppVersion",
  "tgWebAppPlatform",
  "tgWebAppThemeParams",
] as const;

export const pwaInstallState = $state<PwaInstallSnapshot>({
  state: "unknown",
  platform: "other",
  canPrompt: false,
});

let initialized = false;
let installed = false;
let embeddedByUa = false;
let embeddedByTelegram = false;
let manifestAvailable = false;
let promptCapable = true;
let deferredPrompt: BeforeInstallPromptEvent | null = null;
let offerShownThisSession = false;
let signalScreen: PwaInstallSignalScreen = "home";
let removeListeners: (() => void) | null = null;
let generation = 0;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getNavigator(): Navigator | null {
  return typeof navigator === "undefined" ? null : navigator;
}

function detectPlatform(): PwaInstallPlatform {
  const nav = getNavigator();
  const ua = String(nav?.userAgent || "");
  const userAgentData = (nav as (Navigator & { userAgentData?: { platform?: string } }) | null)
    ?.userAgentData;
  const platform = String(userAgentData?.platform || nav?.platform || "").toLowerCase();
  if (/android/i.test(ua)) return "android";
  const isIpadDesktopUa = /macintosh/i.test(ua) && Number(nav?.maxTouchPoints || 0) > 1;
  if (/iphone|ipad|ipod/i.test(ua) || isIpadDesktopUa || /ipad|iphone|ipod/.test(platform)) {
    return "ios";
  }
  if (
    /windows|macintosh|linux|cros|chrome os/i.test(ua) ||
    /win|mac|linux|cros|chrome os/.test(platform)
  ) {
    return "desktop";
  }
  return "other";
}

function detectEmbeddedWebView(): boolean {
  const ua = String(getNavigator()?.userAgent || "");
  return EMBEDDED_UA_PATTERN.test(ua);
}

/**
 * Firefox and desktop Safari never fire `beforeinstallprompt`; they get
 * permanent guidance instead of a dead install button. Chromium variants
 * (Chrome, Edge, Opera, Samsung Internet) stay prompt-capable.
 */
function detectPromptCapability(): boolean {
  const ua = String(getNavigator()?.userAgent || "");
  if (/firefox|fxios/i.test(ua)) return false;
  return !(/safari/i.test(ua) && !/chrome|chromium|crios|edg|opr|android/i.test(ua));
}

function hasTelegramLaunchParams(): boolean {
  try {
    if (typeof window === "undefined") return false;
    const telegramInitData = window.Telegram?.WebApp?.initData;
    if (typeof telegramInitData === "string" && telegramInitData.trim()) return true;
    const query = new URLSearchParams(window.location.search.replace(/^\?/, ""));
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    if (TELEGRAM_LAUNCH_KEYS.some((key) => query.has(key) || hash.has(key))) return true;
    // The official SDK restores this record after same-tab full-page navigation.
    const raw = window.sessionStorage.getItem("__telegram__initParams");
    if (!raw) return false;
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value)) return false;
    if (typeof value.tgWebAppData === "string" && value.tgWebAppData.trim()) return true;
    return (
      typeof value.tgWebAppVersion === "string" &&
      /^\d+\.\d+(?:\.\d+)?$/.test(value.tgWebAppVersion) &&
      typeof value.tgWebAppPlatform === "string" &&
      Boolean(value.tgWebAppPlatform.trim()) &&
      value.tgWebAppPlatform !== "unknown"
    );
  } catch {
    return false;
  }
}

function detectManifest(): boolean {
  try {
    // Outside a browser there is no shell to inspect; keep the state usable
    // for unit tests and non-DOM callers instead of forcing pwa-disabled.
    if (typeof document === "undefined") return true;
    const byId =
      typeof document.getElementById === "function"
        ? document.getElementById("app-manifest")
        : null;
    const bySelector =
      typeof document.querySelector === "function"
        ? document.querySelector('link[rel="manifest"]')
        : null;
    return Boolean(byId || bySelector);
  } catch {
    return false;
  }
}

function detectStandaloneDisplay(): boolean {
  try {
    if (typeof window !== "undefined" && window.matchMedia("(display-mode: standalone)").matches) {
      return true;
    }
  } catch {
    // matchMedia is optional in embedded/synthetic environments.
  }
  const nav = getNavigator() as (Navigator & { standalone?: boolean }) | null;
  return nav?.standalone === true;
}

function getLocalStorage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage || null;
  } catch {
    return null;
  }
}

type OfferRead = { record: OfferRecord | null; failed: boolean };

function readOffer(): OfferRead {
  try {
    const storage = getLocalStorage();
    if (!storage) return { record: null, failed: true };
    const raw = storage.getItem(PWA_INSTALL_OFFER_STORAGE_KEY);
    if (!raw) return { record: null, failed: false };
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      return { record: null, failed: false };
    }
    if (!isRecord(parsed)) return { record: null, failed: false };
    const shownAt = parsed.shownAt;
    const outcome = parsed.outcome;
    if (typeof shownAt !== "number" || !Number.isFinite(shownAt) || shownAt <= 0) {
      return { record: null, failed: false };
    }
    if (typeof outcome !== "string" || !OFFER_OUTCOMES.has(outcome)) {
      return { record: null, failed: false };
    }
    return { record: { shownAt, outcome: outcome as PwaOfferOutcome }, failed: false };
  } catch {
    return { record: null, failed: true };
  }
}

function writeOffer(record: OfferRecord): void {
  try {
    const storage = getLocalStorage();
    if (!storage) return;
    storage.setItem(PWA_INSTALL_OFFER_STORAGE_KEY, JSON.stringify(record));
  } catch {
    // Storage may be unavailable (private mode, quotas); the caller keeps a
    // session flag so the offer cannot loop during this page load.
  }
}

function writeOfferOutcome(outcome: PwaOfferOutcome, createIfMissing = false): void {
  const existing = readOffer().record;
  if (!existing && !createIfMissing) return;
  writeOffer({ shownAt: existing?.shownAt ?? Date.now(), outcome });
  offerShownThisSession = true;
}

function reportPwaSignal(action: PwaInstallSignalAction): void {
  try {
    reportOnboardingAction(action, signalScreen, "app");
  } catch {
    // Analytics must never break installation or navigation.
  }
}

function resolveState(): PwaInstallState {
  if (installed) return "installed";
  if (embeddedByUa || embeddedByTelegram) return "embedded";
  if (!manifestAvailable) return "pwa-disabled";
  if (deferredPrompt) return "prompt-available";
  if (pwaInstallState.platform === "ios" || !promptCapable) return "guidance-only";
  return "unknown";
}

function refreshState(): void {
  pwaInstallState.canPrompt = false;
  pwaInstallState.state = resolveState();
  pwaInstallState.canPrompt = pwaInstallState.state === "prompt-available";
}

function checkInstalledRelatedApps(): void {
  const nav = getNavigator() as
    (Navigator & { getInstalledRelatedApps?: () => Promise<unknown[]> }) | null;
  if (!nav || typeof nav.getInstalledRelatedApps !== "function") return;
  const capturedGeneration = generation;
  try {
    void nav
      .getInstalledRelatedApps()
      .then((apps) => {
        if (capturedGeneration !== generation) return;
        if (!Array.isArray(apps) || apps.length === 0) return;
        installed = true;
        writeOfferOutcome("installed", true);
        refreshState();
      })
      .catch(() => {
        // Best effort only; unsupported/failed checks keep the current state.
      });
  } catch {
    // Some embedded Chromium builds expose the method but reject the call.
  }
}

function attachListeners(): void {
  const win = window;
  if (typeof win.addEventListener !== "function") return;
  const onBeforeInstallPrompt = (event: Event) => {
    const promptEvent = event as BeforeInstallPromptEvent;
    promptEvent.preventDefault();
    if (installed && detectStandaloneDisplay()) return;
    // A fresh browser prompt is evidence the app can be installed again
    // (for example after an uninstall); retire the saved installation hint.
    if (installed) writeOfferOutcome("shown", true);
    installed = false;
    deferredPrompt = promptEvent;
    refreshState();
  };
  const onAppInstalled = () => {
    installed = true;
    deferredPrompt = null;
    refreshState();
    writeOfferOutcome("installed", true);
    reportPwaSignal("pwa_app_installed");
  };
  const onDisplayModeChange = (event: MediaQueryListEvent) => {
    if (!event.matches) return;
    installed = true;
    writeOfferOutcome("installed", true);
    refreshState();
  };

  win.addEventListener("beforeinstallprompt", onBeforeInstallPrompt);
  win.addEventListener("appinstalled", onAppInstalled);
  let media: MediaQueryList | null = null;
  try {
    media = win.matchMedia("(display-mode: standalone)");
    media.addEventListener("change", onDisplayModeChange);
  } catch {
    media = null;
  }
  removeListeners = () => {
    if (typeof win.removeEventListener === "function") {
      win.removeEventListener("beforeinstallprompt", onBeforeInstallPrompt);
      win.removeEventListener("appinstalled", onAppInstalled);
    }
    try {
      media?.removeEventListener("change", onDisplayModeChange);
    } catch {
      // The environment may not support media query listeners.
    }
  };
}

/**
 * Capture install state as early as possible. Idempotent: repeated calls keep
 * the first listener set and only refresh the Telegram context when provided.
 */
export function initPwaInstall(options: { telegramMiniApp?: boolean } = {}): void {
  if (typeof window === "undefined") return;
  if (initialized) {
    if (options.telegramMiniApp !== undefined) {
      embeddedByTelegram = Boolean(options.telegramMiniApp);
      refreshState();
    }
    return;
  }
  initialized = true;
  pwaInstallState.platform = detectPlatform();
  manifestAvailable = detectManifest();
  embeddedByUa = detectEmbeddedWebView();
  embeddedByTelegram = options.telegramMiniApp ?? hasTelegramLaunchParams();
  promptCapable = detectPromptCapability();
  installed = detectStandaloneDisplay() || readOffer().record?.outcome === "installed";
  if (installed) writeOfferOutcome("installed", true);
  attachListeners();
  refreshState();
  if (!installed) checkInstalledRelatedApps();
}

export function getPwaInstallSnapshot(): PwaInstallSnapshot {
  return {
    state: pwaInstallState.state,
    platform: pwaInstallState.platform,
    canPrompt: pwaInstallState.canPrompt,
  };
}

/** Settings action and install cards share the same platform eligibility. */
export function isPwaInstallAvailable(state: PwaInstallState = pwaInstallState.state): boolean {
  return state !== "installed" && state !== "embedded" && state !== "pwa-disabled";
}

export function setPwaInstallTelegramContext(telegramMiniApp: boolean): void {
  if (typeof window === "undefined") return;
  initPwaInstall({ telegramMiniApp });
  embeddedByTelegram = Boolean(telegramMiniApp);
  refreshState();
}

export function setPwaInstallAnalyticsScreen(screen: PwaInstallSignalScreen): void {
  signalScreen = screen;
}

export function reportPwaInstallClicked(screen: PwaInstallSignalScreen): void {
  signalScreen = screen;
  reportPwaSignal("pwa_install_clicked");
}

export function hasPwaOfferBeenShown(): boolean {
  const read = readOffer();
  if (read.record) {
    offerShownThisSession = true;
    return true;
  }
  if (read.failed) return offerShownThisSession;
  // A readable, cleared key is an intentional reset, not a storage failure.
  offerShownThisSession = false;
  return false;
}

export function shouldShowPwaInstallOffer(): boolean {
  return isPwaInstallAvailable() && !hasPwaOfferBeenShown();
}

/** Mark the offer shown; safe to call before the user interacts with it. */
export function markPwaOfferShown(): void {
  const read = readOffer();
  if (!read.record) writeOffer({ shownAt: Date.now(), outcome: "shown" });
  // Remember the successful show/read even when storage later becomes
  // inaccessible; a readable, cleared key still resets it in
  // `hasPwaOfferBeenShown`.
  offerShownThisSession = true;
}

export function markPwaOfferDismissed(): void {
  writeOfferOutcome("dismissed", true);
}

/**
 * Call `prompt()` only from an explicit user click. The deferred event is
 * single-use, so it is cleared before the browser UI opens.
 */
export async function promptPwaInstall(): Promise<"accepted" | "dismissed" | "unavailable"> {
  const promptEvent = deferredPrompt;
  if (!promptEvent || pwaInstallState.state !== "prompt-available") return "unavailable";
  if (typeof promptEvent.prompt !== "function" || !promptEvent.userChoice) return "unavailable";
  deferredPrompt = null;
  refreshState();
  try {
    await promptEvent.prompt();
    const choice = await promptEvent.userChoice;
    if (choice?.outcome === "accepted") {
      writeOfferOutcome("accepted", true);
      reportPwaSignal("pwa_install_prompt_accepted");
      return "accepted";
    }
    if (choice?.outcome === "dismissed") {
      writeOfferOutcome("dismissed", true);
      reportPwaSignal("pwa_install_prompt_dismissed");
      return "dismissed";
    }
    reportPwaSignal("pwa_install_unconfirmed");
    return "unavailable";
  } catch {
    reportPwaSignal("pwa_install_unconfirmed");
    return "unavailable";
  }
}

/** Reset listeners/state without touching persisted offer data (tests and HMR). */
export function resetPwaInstall(): void {
  generation += 1;
  removeListeners?.();
  removeListeners = null;
  initialized = false;
  installed = false;
  embeddedByUa = false;
  embeddedByTelegram = false;
  manifestAvailable = false;
  promptCapable = true;
  deferredPrompt = null;
  offerShownThisSession = false;
  signalScreen = "home";
  pwaInstallState.state = "unknown";
  pwaInstallState.platform = "other";
  pwaInstallState.canPrompt = false;
}
