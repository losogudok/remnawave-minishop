/**
 * Vendor-neutral host for onboarding signal integrations (ONB-04 Core contract).
 *
 * Deployment-enabled integrations initialize on guest, public and
 * authenticated surfaces and observe allowlisted navigation/actions through
 * explicit Core calls. The plugin layer owns settings, persistence and
 * delivery (ONB-05); this module only collects descriptors from the public
 * bootstrap, injects declared vendor scripts after advertising attribution
 * is ready, and forwards sanitized signals.
 *
 * Funnel mapping (frontend signals vs authoritative backend events):
 *
 * - `screen_viewed` with `screen: "install" | "public_install"` means the
 *   instructions were viewed.
 * - `external_opened` / `app_opened` on install screens approximate client
 *   download clicks; `connect_opened` / `text_copied` on install screens
 *   approximate subscription import / copy clicks.
 * - `auth_completed` marks a guest-to-auth transition. Authoritative
 *   registration is the backend `user.registered` event, never this signal.
 * - `trial_activated` is reported only from a confirmed API result.
 *   Confirmed business outcomes (payment fulfillment, first VPN connection)
 *   must use backend domain events (`payment.succeeded`,
 *   `panel.webhook_received` with `user.first_connected`); applying an
 *   activation code or opening checkout is not fulfillment.
 *
 * Guarantees: no visitor consent prompt (collection starts when the
 * administrator enabled the integration); disabled integrations cause zero
 * third-party requests; provider failures never block authentication,
 * checkout, instructions or navigation; no Core DOM scraping and no global
 * fetch/history interception — everything flows through explicit calls.
 */

export type OnboardingSignalSurface = "guest" | "public" | "app";

export type OnboardingSignalScreen =
  | "login"
  | "public_install"
  | "home"
  | "install"
  | "trial"
  | "invite"
  | "partner"
  | "devices"
  | "support"
  | "settings"
  | "notifications"
  | "payment_history"
  | "security"
  | "status"
  | "extensions";

export type OnboardingSignalAction =
  | "screen_viewed"
  | "connect_opened"
  | "app_opened"
  | "external_opened"
  | "text_copied"
  | "share_opened"
  | "trial_activated"
  | "auth_completed";

export type OnboardingSignalDescriptor = {
  owner: string;
  id: string;
  entry: string;
  scripts: string[];
  connect_hosts: string[];
  config: Record<string, string | number | boolean>;
};

export type OnboardingAttribution = {
  code: string;
  utm: Record<string, string>;
};

export type OnboardingSignal = {
  event: string;
  action: OnboardingSignalAction;
  screen: OnboardingSignalScreen;
  surface: OnboardingSignalSurface;
  ad_code: string;
  utm_source: string;
  utm_medium: string;
  utm_campaign: string;
  utm_content: string;
  utm_term: string;
};

export type OnboardingSignalAdapter = {
  unmount?: () => void;
};

export type OnboardingSignalAdapterModule = {
  mountOnboardingSignals: (
    host: OnboardingSignalAdapterHost
  ) => OnboardingSignalAdapter | Promise<OnboardingSignalAdapter>;
};

export type OnboardingSignalAdapterHost = {
  getAttribution: () => OnboardingAttribution;
  isAuthenticated: () => boolean;
  onSignal: (listener: (signal: OnboardingSignal) => void) => () => void;
};

type SignalListener = (signal: OnboardingSignal) => void;

type HostState = {
  initialized: boolean;
  descriptors: OnboardingSignalDescriptor[];
  attribution: OnboardingAttribution;
  reportedViews: Set<string>;
  authReported: boolean;
  trialReported: boolean;
  authenticated: boolean;
  adapters: OnboardingSignalAdapter[];
  listeners: Set<SignalListener>;
  failedScripts: Set<string>;
  signalHistory: OnboardingSignal[];
};

const MAX_DESCRIPTORS = 4;
const MAX_SCRIPTS = 4;
const MAX_CONNECT_HOSTS = 8;
const MAX_CONFIG_KEYS = 16;
const MAX_CONFIG_STRING = 256;
const MAX_SIGNAL_HISTORY = 20;
const MAX_ATTRIBUTION_VALUE_LENGTH = 64;
const OWNER_ID_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;
const CONFIG_KEY_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"] as const;
const CAMPAIGN_CODE_PATTERN = /^[A-Za-z0-9_-]{2,64}$/;
const ATTRIBUTION_VALUE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const NUMERIC_ID_PATTERN = /^\d+$/;
const URL_REMNANT_PATTERN = /https?|www\.|\.com|\.net|\.ru|\.io|\.org/i;
const OPAQUE_ALNUM_TOKEN_PATTERN = /^[A-Za-z0-9]+$/;
const SCRIPT_ATTRIBUTE = "data-onboarding-signal";

const SCREENS: ReadonlySet<string> = new Set([
  "login",
  "public_install",
  "home",
  "install",
  "trial",
  "invite",
  "partner",
  "devices",
  "support",
  "settings",
  "notifications",
  "payment_history",
  "security",
  "status",
  "extensions",
]);

const ACTIONS: ReadonlySet<string> = new Set([
  "screen_viewed",
  "connect_opened",
  "app_opened",
  "external_opened",
  "text_copied",
  "share_opened",
  "trial_activated",
  "auth_completed",
]);

function emptyAttribution(): OnboardingAttribution {
  return { code: "", utm: {} };
}

function createState(): HostState {
  return {
    initialized: false,
    descriptors: [],
    attribution: emptyAttribution(),
    reportedViews: new Set(),
    authReported: false,
    trialReported: false,
    authenticated: false,
    adapters: [],
    listeners: new Set(),
    failedScripts: new Set(),
    signalHistory: [],
  };
}

let state: HostState = createState();
let initEpoch = 0;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validHostname(hostname: string): boolean {
  return (
    hostname.length > 0 &&
    hostname.length <= 253 &&
    /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/i.test(
      hostname
    )
  );
}

function validScriptUrl(url: string): boolean {
  if (typeof url !== "string" || url.length === 0 || url.length > 256) return false;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  return (
    parsed.protocol === "https:" &&
    !parsed.username &&
    !parsed.password &&
    validHostname(parsed.hostname)
  );
}

function validConnectHost(origin: string): boolean {
  if (typeof origin !== "string" || origin.length === 0 || origin.length > 253) return false;
  let parsed: URL;
  try {
    parsed = new URL(origin);
  } catch {
    return false;
  }
  return (
    parsed.protocol === "https:" &&
    !parsed.username &&
    !parsed.password &&
    parsed.pathname === "/" &&
    !parsed.search &&
    !parsed.hash &&
    validHostname(parsed.hostname)
  );
}

function validEntry(owner: string, entry: string): boolean {
  if (entry === "") return true;
  if (typeof entry !== "string" || entry.length > 256 || entry.includes("\\")) return false;
  if (!entry.startsWith(`/api/plugins/${owner}/`)) return false;
  const path = entry.split("?", 1)[0].split("#", 1)[0];
  return path
    .split("/")
    .slice(1)
    .every((part) => part !== "" && part !== "." && part !== "..");
}

function sanitizeConfig(value: unknown): Record<string, string | number | boolean> | null {
  if (value === undefined || value === null) return {};
  if (!isRecord(value)) return null;
  const keys = Object.keys(value);
  if (keys.length > MAX_CONFIG_KEYS) return null;
  const cleaned: Record<string, string | number | boolean> = {};
  for (const key of keys) {
    if (!CONFIG_KEY_PATTERN.test(key)) return null;
    const item = value[key];
    if (typeof item === "string") {
      if (item.length > MAX_CONFIG_STRING) return null;
      cleaned[key] = item;
    } else if (typeof item === "number") {
      if (!Number.isFinite(item)) return null;
      cleaned[key] = item;
    } else if (typeof item === "boolean") {
      cleaned[key] = item;
    } else {
      return null;
    }
  }
  return cleaned;
}

function parseDescriptor(value: unknown): OnboardingSignalDescriptor | null {
  if (!isRecord(value)) return null;
  const { owner, id, entry, scripts, connect_hosts, connectHosts, config } = value as Record<
    string,
    unknown
  >;
  if (typeof owner !== "string" || !OWNER_ID_PATTERN.test(owner)) return null;
  if (typeof id !== "string" || !OWNER_ID_PATTERN.test(id)) return null;
  const entryValue = entry === undefined ? "" : entry;
  if (typeof entryValue !== "string" || !validEntry(owner, entryValue)) return null;
  const rawScripts = scripts === undefined ? [] : scripts;
  const rawHosts = (connect_hosts ?? connectHosts ?? []) as unknown;
  if (!Array.isArray(rawScripts) || rawScripts.length > MAX_SCRIPTS) return null;
  if (!Array.isArray(rawHosts) || rawHosts.length > MAX_CONNECT_HOSTS) return null;
  const cleanScripts: string[] = [];
  for (const item of rawScripts) {
    if (typeof item !== "string" || !validScriptUrl(item)) return null;
    cleanScripts.push(item);
  }
  const cleanHosts: string[] = [];
  for (const item of rawHosts) {
    if (typeof item !== "string" || !validConnectHost(item)) return null;
    cleanHosts.push(item);
  }
  const cleanConfig = sanitizeConfig(config);
  if (cleanConfig === null) return null;
  if (!entryValue && cleanScripts.length === 0 && cleanHosts.length === 0) return null;
  return {
    owner,
    id,
    entry: entryValue,
    scripts: cleanScripts,
    connect_hosts: cleanHosts,
    config: cleanConfig,
  };
}

/** Parse and validate public bootstrap descriptors; invalid entries are dropped. */
export function parseOnboardingSignalDescriptors(value: unknown): OnboardingSignalDescriptor[] {
  if (!Array.isArray(value)) return [];
  const descriptors: OnboardingSignalDescriptor[] = [];
  for (const item of value.slice(0, MAX_DESCRIPTORS)) {
    const parsed = parseDescriptor(item);
    if (parsed && !descriptors.some((d) => d.owner === parsed.owner && d.id === parsed.id)) {
      descriptors.push(parsed);
    }
  }
  return descriptors;
}

/**
 * Opaque token fence: long or high-entropy strings are subscription/share
 * tokens, not campaign slugs. Short readable slugs (`summer_sale`,
 * `summer_sale-2024`) pass; pure digits (Telegram IDs), long blobs and
 * mixed-case alphanumeric tokens are dropped even when they match the
 * character allowlist. Base64url blobs (`_`/`-`) with mixed case are
 * opaque even without digits and even below 24 chars.
 */
function looksLikeOpaqueToken(value: string): boolean {
  if (value.length >= 32) return true;
  if (value.length >= 20 && OPAQUE_ALNUM_TOKEN_PATTERN.test(value)) {
    const hasDigit = /\d/.test(value);
    const hasLower = /[a-z]/.test(value);
    const hasUpper = /[A-Z]/.test(value);
    if ((hasDigit && (hasLower || hasUpper)) || (hasLower && hasUpper)) return true;
    if (value.length >= 24) return true;
  }
  if (value.length >= 22 && /^[A-Za-z0-9_-]+$/.test(value)) {
    const hasLower = /[a-z]/.test(value);
    const hasUpper = /[A-Z]/.test(value);
    // Mixed-case base64url strings >=22 chars are opaque tokens regardless
    // of digit presence: `wMqa_ksnJrPxLvtYzHcBeFUG` (24, no digits) and
    // `Ab3dE5fG7hJ9kL2mN4pQ_r` (22, with digits) both drop here, while
    // lowercase readable slugs (`summer_sale`, `summer_sale-2024`,
    // `actual-source`) have no uppercase and stay allowed.
    if (hasLower && hasUpper) return true;
  }
  return false;
}

function isSafeAttributionValue(value: unknown): value is string {
  if (typeof value !== "string") return false;
  if (value.length === 0 || value.length > MAX_ATTRIBUTION_VALUE_LENGTH) return false;
  // PII exfiltration fence: emails, URLs, token paths and opaque blobs never
  // become signals even when they arrive via utm params.
  if (value.includes("@") || value.includes("/") || value.includes(":") || value.includes("\\")) {
    return false;
  }
  if (/\s/.test(value)) return false;
  if (/^https?:\/\//i.test(value)) return false;
  // Numeric-only IDs (Telegram user IDs) are identifiers, never campaigns.
  if (NUMERIC_ID_PATTERN.test(value)) return false;
  // Scheme/URL remnants without delimiters (e.g. `https_example`, `www_x`).
  if (URL_REMNANT_PATTERN.test(value)) return false;
  // Percent-encoded delimiters from URLs/tokens.
  if (value.includes("%")) return false;
  if (!ATTRIBUTION_VALUE_PATTERN.test(value)) return false;
  if (looksLikeOpaqueToken(value)) return false;
  return true;
}

function isSafeCampaignCode(value: unknown): value is string {
  if (typeof value !== "string") return false;
  if (!CAMPAIGN_CODE_PATTERN.test(value)) return false;
  if (value.includes("@") || value.includes("/") || value.includes(":")) return false;
  if (NUMERIC_ID_PATTERN.test(value)) return false;
  if (URL_REMNANT_PATTERN.test(value)) return false;
  if (value.includes("%")) return false;
  if (looksLikeOpaqueToken(value)) return false;
  return true;
}

/** Keep only campaign code + known utm keys; raw URLs/tokens never pass through. */
export function sanitizeOnboardingAttribution(value: unknown): OnboardingAttribution {
  if (!isRecord(value)) return emptyAttribution();
  const rawCode = value.code;
  const code = isSafeCampaignCode(rawCode) ? (rawCode as string) : "";
  const rawUtm = isRecord(value.utm) ? value.utm : {};
  const utm: Record<string, string> = {};
  for (const key of UTM_KEYS) {
    const item = rawUtm[key];
    if (isSafeAttributionValue(item)) {
      utm[key] = item;
    }
  }
  return { code, utm };
}

/**
 * Map the shell mode/screen to a safe signal name, or null when the screen
 * is not allowlisted. Unknown screens are never reported.
 */
export function toOnboardingScreen(
  mode: string,
  screen: string
): { screen: OnboardingSignalScreen; surface: OnboardingSignalSurface } | null {
  if (mode === "login") return { screen: "login", surface: "guest" };
  if (mode === "publicInstall") return { screen: "public_install", surface: "public" };
  if (mode !== "app") return null;
  const normalized = String(screen || "")
    .trim()
    .replace(/-/g, "_");
  if (!SCREENS.has(normalized) || normalized === "login" || normalized === "public_install") {
    return null;
  }
  return { screen: normalized as OnboardingSignalScreen, surface: "app" };
}

/** Validate an action name coming from plugin views; unknown names are dropped. */
export function toOnboardingAction(value: string): OnboardingSignalAction | null {
  return ACTIONS.has(value) ? (value as OnboardingSignalAction) : null;
}

function getWindow(): (typeof globalThis & { dataLayer?: unknown[] }) | null {
  if (typeof window === "undefined") return null;
  return window as typeof globalThis & { dataLayer?: unknown[] };
}

function pushToDataLayer(signal: OnboardingSignal): void {
  const w = getWindow();
  if (!w) return;
  try {
    const layer = Array.isArray(w.dataLayer) ? w.dataLayer : [];
    if (!Array.isArray(w.dataLayer)) {
      w.dataLayer = layer;
    }
    layer.push({ ...signal });
  } catch {
    // Analytics delivery never breaks the product shell.
  }
}

function replayHistoryFor(
  capturedState: HostState,
  capturedEpoch: number,
  deliver: SignalListener
): void {
  if (capturedEpoch !== initEpoch || capturedState !== state) return;
  for (const signal of [...capturedState.signalHistory]) {
    if (capturedEpoch !== initEpoch || capturedState !== state) return;
    try {
      deliver({ ...signal });
    } catch {
      // A failing adapter listener must not affect other integrations.
    }
  }
}

function emit(signal: OnboardingSignal): void {
  const key = `${signal.action}:${signal.screen}:${signal.surface}:${signal.event}`;
  void key;
  state.signalHistory.push({ ...signal });
  if (state.signalHistory.length > MAX_SIGNAL_HISTORY) {
    state.signalHistory.splice(0, state.signalHistory.length - MAX_SIGNAL_HISTORY);
  }
  pushToDataLayer(signal);
  for (const listener of [...state.listeners]) {
    try {
      listener(signal);
    } catch {
      // A failing adapter listener must not affect other integrations.
    }
  }
}

function buildSignal(
  action: OnboardingSignalAction,
  screen: OnboardingSignalScreen,
  surface: OnboardingSignalSurface
): OnboardingSignal {
  return {
    event: `minishop_${action}`,
    action,
    screen,
    surface,
    ad_code: state.attribution.code,
    utm_source: state.attribution.utm.utm_source || "",
    utm_medium: state.attribution.utm.utm_medium || "",
    utm_campaign: state.attribution.utm.utm_campaign || "",
    utm_content: state.attribution.utm.utm_content || "",
    utm_term: state.attribution.utm.utm_term || "",
  };
}

function injectScript(descriptor: OnboardingSignalDescriptor, url: string): void {
  const key = `${descriptor.owner}:${descriptor.id}:${url}`;
  if (state.failedScripts.has(key)) return;
  const doc = typeof document === "undefined" ? null : document;
  if (!doc) return;
  try {
    const existing = doc.querySelector(`script[${SCRIPT_ATTRIBUTE}="${key}"]`);
    if (existing) return;
    const element = doc.createElement("script");
    element.src = url;
    element.async = true;
    element.setAttribute(SCRIPT_ATTRIBUTE, key);
    element.addEventListener("error", () => {
      state.failedScripts.add(key);
      element.remove();
    });
    doc.head.appendChild(element);
  } catch {
    // Script injection failures are isolated per integration.
  }
}

function loadAdapter(
  descriptor: OnboardingSignalDescriptor,
  epoch: number,
  ownerState: HostState
): void {
  if (!descriptor.entry) return;
  const host: OnboardingSignalAdapterHost = {
    getAttribution: () => ({
      code: ownerState.attribution.code,
      utm: { ...ownerState.attribution.utm },
    }),
    isAuthenticated: () => ownerState.authenticated,
    onSignal: (listener: SignalListener) => {
      // Epoch fence for late async mounts: a stale host from before
      // destroy/reinit must not observe the replacement lifecycle. Capture
      // the mount epoch and reject subscribe/replay/delivery when stale.
      if (epoch !== initEpoch || ownerState !== state || !ownerState.initialized) {
        return () => {};
      }
      const capturedEpoch = epoch;
      const capturedState = ownerState;
      const wrapped: SignalListener = (signal) => {
        if (capturedEpoch !== initEpoch || capturedState !== state) return;
        try {
          listener({ ...signal });
        } catch {
          // A failing adapter listener must not affect other integrations.
        }
      };
      capturedState.listeners.add(wrapped);
      // Late async adapters miss the initial visit without replay: deliver
      // buffered signals immediately on subscribe (dedupe via reportedViews
      // at the source, so history holds no duplicates for milestones).
      replayHistoryFor(capturedState, capturedEpoch, wrapped);
      return () => {
        capturedState.listeners.delete(wrapped);
      };
    },
  };
  void import(/* @vite-ignore */ descriptor.entry)
    .then((module: unknown) => {
      if (epoch !== initEpoch || ownerState !== state || !ownerState.initialized) return;
      if (!isRecord(module) || typeof module.mountOnboardingSignals !== "function") return;
      let mounted: unknown;
      try {
        mounted = (
          module.mountOnboardingSignals as OnboardingSignalAdapterModule["mountOnboardingSignals"]
        )(host);
      } catch {
        return;
      }
      void Promise.resolve(mounted)
        .then((adapter: unknown) => {
          if (epoch !== initEpoch || ownerState !== state || !ownerState.initialized) {
            try {
              (adapter as OnboardingSignalAdapter | null)?.unmount?.();
            } catch {
              // Stale adapter cleanup stays isolated.
            }
            return;
          }
          if (isRecord(adapter)) ownerState.adapters.push(adapter as OnboardingSignalAdapter);
        })
        .catch(() => {
          // Adapter mount failures stay isolated.
        });
    })
    .catch(() => {
      // Adapter load failures stay isolated.
    });
}

/**
 * Initialize collection after advertising attribution settled. Safe to call
 * once per page load; repeated calls are ignored. Never throws and never
 * blocks the caller: use `void` and continue booting regardless.
 */
export function initOnboardingSignals(descriptors: unknown, attribution: unknown): void {
  if (state.initialized) return;
  state.initialized = true;
  const epoch = ++initEpoch;
  const ownerState = state;
  ownerState.attribution = sanitizeOnboardingAttribution(attribution);
  ownerState.descriptors = parseOnboardingSignalDescriptors(descriptors);
  if (ownerState.descriptors.length === 0) return;
  for (const descriptor of ownerState.descriptors) {
    if (epoch !== initEpoch || ownerState !== state) return;
    for (const url of descriptor.scripts) injectScript(descriptor, url);
    loadAdapter(descriptor, epoch, ownerState);
  }
}

/** Report a screen view; each (screen, surface) pair is reported once per page load. */
export function reportOnboardingScreen(
  screen: OnboardingSignalScreen,
  surface: OnboardingSignalSurface
): void {
  if (!state.initialized || state.descriptors.length === 0) return;
  const key = `${surface}:${screen}`;
  if (state.reportedViews.has(key)) return;
  state.reportedViews.add(key);
  emit(buildSignal("screen_viewed", screen, surface));
}

/** Report a user action; milestones deduplicate, click actions pass through. */
export function reportOnboardingAction(
  action: Exclude<OnboardingSignalAction, "screen_viewed">,
  screen: OnboardingSignalScreen,
  surface: OnboardingSignalSurface
): void {
  if (!state.initialized || state.descriptors.length === 0) return;
  if (action === "trial_activated") {
    if (state.trialReported) return;
    state.trialReported = true;
  }
  if (action === "auth_completed") {
    if (state.authReported) return;
    state.authReported = true;
  }
  emit(buildSignal(action, screen, surface));
}

/**
 * Mark the session authenticated so adapters can join tag-generated browser
 * context to the account. Never reports by itself; the guest-to-auth
 * transition is reported explicitly with `auth_completed`.
 */
export function setOnboardingAuthenticated(): void {
  state.authenticated = true;
}

/**
 * Reset logout/account-transition semantics: adapters see logged-out state,
 * and the next account's auth/trial milestones re-emit instead of being
 * suppressed by the previous account's flags. Views and attribution persist
 * per page load; stale milestone history is dropped so replay cannot leak
 * the previous account's `auth_completed`/`trial_activated`.
 */
export function resetOnboardingAuth(): void {
  state.authenticated = false;
  state.authReported = false;
  state.trialReported = false;
  state.signalHistory = state.signalHistory.filter(
    (signal) => signal.action !== "auth_completed" && signal.action !== "trial_activated"
  );
}

/** Subscribe an adapter to sanitized signals; returns an unsubscribe function. */
export function subscribeOnboardingSignals(listener: SignalListener): () => void {
  // Epoch fence mirrors the adapter host: capture the generation at
  // subscribe so a stale subscription from before destroy/reinit never
  // replays or receives the replacement lifecycle.
  const capturedEpoch = initEpoch;
  const capturedState = state;
  const wrapped: SignalListener = (signal) => {
    if (capturedEpoch !== initEpoch || capturedState !== state) return;
    try {
      listener({ ...signal });
    } catch {
      // A failing adapter listener must not affect other integrations.
    }
  };
  capturedState.listeners.add(wrapped);
  replayHistoryFor(capturedState, capturedEpoch, wrapped);
  return () => {
    capturedState.listeners.delete(wrapped);
  };
}

/** Tear down injected scripts and adapters; also used to reset state in tests. */
export function destroyOnboardingSignals(): void {
  initEpoch++;
  try {
    if (typeof document !== "undefined") {
      for (const element of Array.from(document.querySelectorAll(`script[${SCRIPT_ATTRIBUTE}]`))) {
        element.remove();
      }
    }
  } catch {
    // Cleanup best effort only.
  }
  for (const adapter of state.adapters) {
    try {
      adapter.unmount?.();
    } catch {
      // Adapter cleanup failures stay isolated.
    }
  }
  state = createState();
}

function isHttpSignalUrl(url: unknown): boolean {
  return typeof url === "string" && /^https?:\/\//i.test(url.trim());
}

/**
 * Scheme fallback only: `https://` downloads vs non-http app/import deep
 * links. Prefer {@link resolveInstallSignalKind} at the instruction
 * boundary, which uses explicit button semantics (resource primary-
 * subscription / subscriptionLink vs external) so an `https://`
 * subscription import is not misreported as a download and a `market://`
 * store download is not misreported as an import.
 */
export function classifyInstallOpenTarget(url: string): "download" | "import" {
  return isHttpSignalUrl(url) ? "download" : "import";
}

export type InstallOpenTargetInfo = {
  kind?: string;
  resourceId?: string;
  representation?: string;
} | null;

/**
 * Resolve download vs import from explicit instruction semantics, not the
 * URL scheme. A `primary-subscription` resource (or `subscriptionLink`
 * button) is an import even when its representation is `https://`; an
 * `external` button is a client download even when its scheme is
 * non-https (e.g. `market://`). The URL scheme is only a fallback when no
 * semantic target info is available.
 */
export function resolveInstallSignalKind(options: {
  buttonType?: string;
  target?: InstallOpenTargetInfo;
  url: string;
}): "download" | "import" {
  const target = options.target;
  if (
    target &&
    typeof target === "object" &&
    target.kind === "resource" &&
    target.resourceId === "primary-subscription"
  ) {
    return "import";
  }
  if (options.buttonType === "subscriptionLink") return "import";
  if (options.buttonType === "external") return "download";
  return classifyInstallOpenTarget(options.url);
}

export type InstallInteractionKind = "download" | "import" | "copy" | "share";

/**
 * Explicit semantic reporting at the instruction action boundary. The caller
 * (install screen) knows whether the user tapped download vs import vs copy
 * vs share; only safe fields (action + screen + surface) enter the signal,
 * never URLs, text or tokens.
 */
export function reportInstallInteraction(
  kind: InstallInteractionKind,
  screen: OnboardingSignalScreen,
  surface: OnboardingSignalSurface
): void {
  switch (kind) {
    case "download":
      reportOnboardingAction("external_opened", screen, surface);
      break;
    case "import":
      reportOnboardingAction("app_opened", screen, surface);
      break;
    case "copy":
      reportOnboardingAction("text_copied", screen, surface);
      break;
    case "share":
      reportOnboardingAction("share_opened", screen, surface);
      break;
  }
}

export type OnboardingReportableActions = {
  openConnectLink: (...args: never[]) => unknown;
  openPublicConnectLink: (...args: never[]) => unknown;
  openTrialConnectLink: (...args: never[]) => unknown;
  openActivationConnectLink: (...args: never[]) => unknown;
  openAppLink: (url: string) => unknown;
  openExternalLink: (url: string) => unknown;
  copyText: (text: string, message?: string) => Promise<unknown>;
};

export type OnboardingSignalContext = {
  screen: OnboardingSignalScreen;
  surface: OnboardingSignalSurface;
} | null;

function isInstallContext(context: OnboardingSignalContext): boolean {
  return context !== null && (context.screen === "install" || context.screen === "public_install");
}

function reportActionSafe(
  action: Exclude<OnboardingSignalAction, "screen_viewed" | "auth_completed" | "trial_activated">,
  getContext: () => OnboardingSignalContext
): void {
  try {
    const context = getContext();
    if (!context) return;
    // Install screens report explicit semantic actions at the instruction
    // boundary (download vs import, copy vs share); the generic wrapper
    // stays silent there to avoid conflated duplicates.
    if (
      isInstallContext(context) &&
      (action === "app_opened" || action === "external_opened" || action === "text_copied")
    ) {
      return;
    }
    reportOnboardingAction(action, context.screen, context.surface);
  } catch {
    // Reporting never breaks the wrapped product action.
  }
}

/**
 * Wrap shell actions so allowlisted product interactions report sanitized
 * signals (action + screen + surface only; never URLs, text or tokens).
 * The wrapped action always runs, even when reporting fails or is disabled.
 *
 * Download vs import are distinguished by URL scheme: `https://` targets
 * report `external_opened` (download), non-http deep links report
 * `app_opened` (import). Copy vs share are distinguished at the instruction
 * boundary via `reportInstallInteraction` (`text_copied` vs `share_opened`);
 * the generic `copyText` wrapper only reports `text_copied` outside install
 * screens.
 */
export function withOnboardingSignalActions<T extends OnboardingReportableActions>(
  actions: T,
  getContext: () => OnboardingSignalContext
): T {
  const wrapSync = <A extends unknown[]>(
    original: (...args: A) => unknown,
    action: Exclude<OnboardingSignalAction, "screen_viewed" | "auth_completed" | "trial_activated">,
    onlyWhenTruthy: boolean
  ) => {
    return (...args: A): unknown => {
      let result: unknown;
      try {
        result = original(...args);
      } finally {
        if (!onlyWhenTruthy || result) reportActionSafe(action, getContext);
      }
      return result;
    };
  };
  const wrapAppLink = (original: (url: string) => unknown) => {
    return (url: string): unknown => {
      let result: unknown;
      try {
        result = original(url);
      } finally {
        try {
          const context = getContext();
          if (context && !isInstallContext(context)) {
            reportOnboardingAction(
              isHttpSignalUrl(url) ? "external_opened" : "app_opened",
              context.screen,
              context.surface
            );
          }
        } catch {
          // Reporting never breaks the wrapped product action.
        }
      }
      return result;
    };
  };
  return {
    ...actions,
    openConnectLink: wrapSync(
      actions.openConnectLink as (...args: never[]) => unknown,
      "connect_opened",
      true
    ),
    openPublicConnectLink: wrapSync(
      actions.openPublicConnectLink as (...args: never[]) => unknown,
      "connect_opened",
      true
    ),
    openTrialConnectLink: wrapSync(
      actions.openTrialConnectLink as (...args: never[]) => unknown,
      "connect_opened",
      true
    ),
    openActivationConnectLink: wrapSync(
      actions.openActivationConnectLink as (...args: never[]) => unknown,
      "connect_opened",
      true
    ),
    openAppLink: wrapAppLink(actions.openAppLink as (url: string) => unknown),
    openExternalLink: wrapSync(
      actions.openExternalLink as (url: string) => unknown,
      "external_opened",
      false
    ),
    copyText: async (text: string, message?: string): Promise<unknown> => {
      try {
        return await actions.copyText(text, message);
      } finally {
        reportActionSafe("text_copied", getContext);
      }
    },
  };
}
