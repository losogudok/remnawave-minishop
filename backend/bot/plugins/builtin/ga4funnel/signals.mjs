/*
 * GA4 onboarding funnel adapter (ONB-05).
 *
 * The Google tag runs in a hidden same-origin frame; the app page never loads
 * gtag.js and never defines window.gtag/dataLayer. This adapter only forwards
 * allowlisted onboarding steps as validated postMessages. Raw app URLs,
 * referrers, tokens, emails, Telegram/user IDs and subscription data never
 * enter the frame.
 */

const CONFIG_PATH = "/api/plugins/ga4funnel/config";
const BIND_PATH = "/api/plugins/ga4funnel/bind";
const FRAME_PATH = "/api/plugins/ga4funnel/frame";
const MEASUREMENT_ID_PATTERN = /^G-[A-Z0-9]{4,20}$/;
const CLIENT_ID_PATTERN = /^[0-9]{1,20}\.[0-9]{1,20}$/;
const SCREENS = new Set([
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
const SURFACES = new Set(["guest", "public", "app"]);
const UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"];
const SAFE_VALUE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const INSTALL_SCREENS = new Set(["install", "public_install"]);
const BIND_RETRY_DELAYS_MS = [200, 400, 800, 1600, 3200];
const MAX_QUEUED_FRAME_EVENTS = 20;
const FRAME_READY_TIMEOUT_MS = 5000;
const FRAME_INIT_RETRY_MS = 250;

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeValue(value, maxLength = 64) {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (!text || text.length > maxLength) return "";
  if (text.includes("@") || text.includes("/") || text.includes(":") || text.includes("%")) {
    return "";
  }
  return SAFE_VALUE_PATTERN.test(text) ? text : "";
}

function safeUtm(attribution) {
  const result = {};
  if (!isRecord(attribution)) return result;
  const utm = isRecord(attribution.utm) ? attribution.utm : {};
  for (const key of UTM_KEYS) {
    const value = safeValue(utm[key]);
    if (value) result[key] = value;
  }
  return result;
}

function safeAttribution(host) {
  let attribution = {};
  try {
    attribution = host.getAttribution();
  } catch {
    attribution = {};
  }
  return {
    code: isRecord(attribution) ? safeValue(attribution.code) : "",
    utm: safeUtm(attribution),
  };
}

function getWindow() {
  return typeof window === "undefined" ? null : window;
}

function appOrigin() {
  const win = getWindow();
  const origin = win && win.location ? String(win.location.origin || "") : "";
  return /^https?:\/\/[^/]+$/i.test(origin) ? origin : "";
}

function safeOrigin() {
  return appOrigin() || "https://minishop.invalid";
}

async function fetchConfig() {
  try {
    const response = await fetch(CONFIG_PATH, {
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response || !response.ok) return null;
    const body = await response.json();
    if (!isRecord(body) || body.enabled !== true) return null;
    const measurementId = typeof body.measurement_id === "string" ? body.measurement_id : "";
    if (!MEASUREMENT_ID_PATTERN.test(measurementId)) return null;
    return {
      measurementId,
      debugMode: body.debug_mode === true,
    };
  } catch {
    return null;
  }
}

function readCookie(name) {
  try {
    const source = typeof document === "undefined" ? "" : String(document.cookie || "");
    for (const part of source.split(";")) {
      const item = part.trim();
      const separator = item.indexOf("=");
      if (separator < 0) continue;
      if (item.slice(0, separator) === name) {
        return decodeURIComponent(item.slice(separator + 1));
      }
    }
  } catch {
    // Cookie access can be blocked; no client ID means no bind request.
  }
  return "";
}

function readGaClientId() {
  const raw = readCookie("_ga");
  const parts = raw.split(".");
  if (parts.length !== 4 || parts[0] !== "GA1") return "";
  const clientId = `${parts[2]}.${parts[3]}`;
  return CLIENT_ID_PATTERN.test(clientId) ? clientId : "";
}

async function postBind(clientId) {
  const headers = { "Content-Type": "application/json" };
  const csrf = readCookie("rw_webapp_csrf");
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const response = await fetch(BIND_PATH, {
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers,
    body: JSON.stringify({ client_id: clientId }),
  });
  return Boolean(response && response.ok);
}

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function pageLocation(screen) {
  return `${safeOrigin()}/__/${screen}`;
}

function baseParams(signal) {
  return {
    page_location: pageLocation(signal.screen),
    page_referrer: "",
    surface: signal.surface,
    screen: signal.screen,
  };
}

function pageViewParams(signal, attribution) {
  return {
    ...baseParams(signal),
    page_title: signal.screen,
    ...(attribution.code ? { ad_code: attribution.code } : {}),
    ...attribution.utm,
  };
}

function mapSignal(signal, attribution) {
  const action = String(signal.action || "");
  const screen = String(signal.screen || "");
  const surface = String(signal.surface || "");
  if (!SCREENS.has(screen) || !SURFACES.has(surface)) return null;
  if (action === "screen_viewed") {
    return { name: "page_view", params: pageViewParams(signal, attribution) };
  }
  if (action === "trial_activated") return null;
  if (action === "external_opened" && INSTALL_SCREENS.has(screen)) {
    return { name: "app_download_click", params: baseParams(signal) };
  }
  if (action === "app_opened" && INSTALL_SCREENS.has(screen)) {
    return { name: "subscription_import_click", params: baseParams(signal) };
  }
  if (action === "text_copied" && INSTALL_SCREENS.has(screen)) {
    return { name: "subscription_link_copy", params: baseParams(signal) };
  }
  if (action === "share_opened" && INSTALL_SCREENS.has(screen)) {
    return { name: "install_guide_share", params: baseParams(signal) };
  }
  if (action === "connect_opened") {
    return { name: "connect_instructions_opened", params: baseParams(signal) };
  }
  if (action === "auth_completed") {
    return { name: "login", params: baseParams(signal) };
  }
  return null;
}

function createFrame() {
  if (typeof document === "undefined") return null;
  const target = document.body || document.documentElement;
  if (!target) return null;
  try {
    const frame = document.createElement("iframe");
    frame.src = FRAME_PATH;
    frame.referrerPolicy = "no-referrer";
    frame.setAttribute("referrerpolicy", "no-referrer");
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin");
    frame.setAttribute("aria-hidden", "true");
    frame.setAttribute("tabindex", "-1");
    frame.style.display = "none";
    frame.style.width = "0";
    frame.style.height = "0";
    frame.style.border = "0";
    target.appendChild(frame);
    return frame;
  } catch {
    return null;
  }
}

export function mountOnboardingSignals(host) {
  let mounted = true;
  let unsubscribe = null;
  let authEpoch = 0;
  let boundEpoch = -1;
  let bindingEpoch = -1;
  let authenticated = false;
  let frame = null;
  let frameOrigin = "";
  let frameReady = false;
  let initRetryTimer = null;
  let readyTimeout = null;
  const queuedEvents = [];

  const isAuthenticated = () => {
    try {
      return host.isAuthenticated() === true;
    } catch {
      return false;
    }
  };

  const observeAuth = () => {
    const current = isAuthenticated();
    if (!current) {
      authenticated = false;
      return false;
    }
    if (!authenticated) {
      authenticated = true;
      authEpoch += 1;
    }
    return true;
  };

  const bindOnce = async () => {
    const epoch = authEpoch;
    if (!mounted || bindingEpoch === epoch || boundEpoch === epoch || !isAuthenticated()) {
      return;
    }
    bindingEpoch = epoch;
    try {
      for (const waitMs of BIND_RETRY_DELAYS_MS) {
        if (!mounted || boundEpoch === epoch || epoch !== authEpoch || !isAuthenticated()) {
          return;
        }
        const clientId = readGaClientId();
        if (clientId) {
          const bound = await postBind(clientId);
          if (bound && epoch === authEpoch && mounted) {
            boundEpoch = epoch;
          }
          return;
        }
        await delay(waitMs);
      }
    } catch {
      // Binding is best effort and retried on a later signal.
    } finally {
      if (bindingEpoch === epoch) bindingEpoch = -1;
    }
  };

  const postToFrame = (message) => {
    if (!frame || !frame.contentWindow || !frameOrigin) return false;
    try {
      frame.contentWindow.postMessage(message, frameOrigin);
      return true;
    } catch {
      return false;
    }
  };

  const sendFrameInit = (measurementId, debugMode) => {
    postToFrame({
      type: "ga4_init",
      measurement_id: measurementId,
      debug_mode: debugMode,
    });
  };

  const flushFrameQueue = () => {
    if (!frameReady) return;
    while (queuedEvents.length > 0) {
      const event = queuedEvents.shift();
      if (event) postToFrame(event);
    }
  };

  const forwardToFrame = (mapped) => {
    const message = {
      type: "ga4_event",
      name: mapped.name,
      params: mapped.params,
    };
    if (!frame) return;
    if (frameReady) {
      postToFrame(message);
      return;
    }
    if (queuedEvents.length < MAX_QUEUED_FRAME_EVENTS) {
      queuedEvents.push(message);
    }
  };

  const onFrameMessage = (event) => {
    if (!mounted || !frame) return;
    try {
      if (!event || event.origin !== frameOrigin) return;
      if (event.source !== frame.contentWindow) return;
      if (!isRecord(event.data) || event.data.type !== "ga4_ready") return;
      frameReady = true;
      if (readyTimeout !== null) {
        clearTimeout(readyTimeout);
        readyTimeout = null;
      }
      if (initRetryTimer !== null) {
        clearInterval(initRetryTimer);
        initRetryTimer = null;
      }
      flushFrameQueue();
    } catch {
      // Malformed frame messages are isolated.
    }
  };

  const stopFrame = () => {
    if (initRetryTimer !== null) {
      clearInterval(initRetryTimer);
      initRetryTimer = null;
    }
    if (readyTimeout !== null) {
      clearTimeout(readyTimeout);
      readyTimeout = null;
    }
    const win = getWindow();
    if (win) {
      try {
        win.removeEventListener("message", onFrameMessage);
      } catch {
        // Listener cleanup is best effort.
      }
    }
    if (frame) {
      try {
        frame.remove();
      } catch {
        // Frame cleanup is best effort.
      }
    }
    frame = null;
    frameOrigin = "";
    frameReady = false;
    queuedEvents.length = 0;
  };

  const onSignal = (signal) => {
    if (!mounted) return;
    try {
      const authed = observeAuth();
      if (!isRecord(signal)) return;
      if (authed && signal.action === "auth_completed") {
        // A login can follow another login without any logged-out signal.
        authEpoch += 1;
      }
      if (authed && boundEpoch !== authEpoch) {
        void bindOnce();
      }
      const mapped = mapSignal(signal, safeAttribution(host));
      if (!mapped) return;
      forwardToFrame(mapped);
    } catch {
      // A malformed signal must not affect the host or other adapters.
    }
  };

  const start = async () => {
    const config = await fetchConfig();
    if (!config || !mounted) return;
    const origin = appOrigin();
    if (!origin) return;
    frameOrigin = origin;
    const win = getWindow();
    if (!win) return;
    try {
      win.addEventListener("message", onFrameMessage);
    } catch {
      return;
    }
    frame = createFrame();
    if (!frame) {
      stopFrame();
      return;
    }
    sendFrameInit(config.measurementId, config.debugMode);
    try {
      frame.onload = () => sendFrameInit(config.measurementId, config.debugMode);
    } catch {
      // The initial postMessage remains enough when load hooks are blocked.
    }
    initRetryTimer = setInterval(() => {
      if (!frameReady) sendFrameInit(config.measurementId, config.debugMode);
    }, FRAME_INIT_RETRY_MS);
    readyTimeout = setTimeout(() => {
      if (!frameReady) stopFrame();
    }, FRAME_READY_TIMEOUT_MS);
    try {
      unsubscribe = host.onSignal(onSignal);
    } catch {
      stopFrame();
      return;
    }
    if (observeAuth()) void bindOnce();
  };

  void start().catch(() => {});

  return {
    unmount() {
      mounted = false;
      try {
        if (typeof unsubscribe === "function") unsubscribe();
      } catch {
        // Unsubscribe failures are isolated.
      }
      unsubscribe = null;
      stopFrame();
    },
  };
}
