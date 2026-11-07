/*
 * GA4 tag host running inside the isolated same-origin frame.
 *
 * The real app page never loads gtag.js. This frame sees only its own synthetic
 * URL, validates every parent message, and always overwrites page_location and
 * page_referrer so Enhanced Measurement cannot observe app URLs or referrers.
 */

const FRAME_SCREEN = "analytics-frame";
const MEASUREMENT_ID_PATTERN = /^G-[A-Z0-9]{4,20}$/;
const SAFE_VALUE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;
const UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"];
const EVENT_NAMES = new Set([
  "page_view",
  "app_download_click",
  "subscription_import_click",
  "subscription_link_copy",
  "install_guide_share",
  "connect_instructions_opened",
  "login",
]);
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

let initialized = false;

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeOrigin() {
  const origin = typeof location === "undefined" ? "" : String(location.origin || "");
  return /^https?:\/\/[^/]+$/i.test(origin) ? origin : "https://minishop.invalid";
}

function pageLocation(screen) {
  return `${safeOrigin()}/__/${screen}`;
}

function frameDefaults() {
  return {
    page_location: pageLocation(FRAME_SCREEN),
    page_referrer: "",
  };
}

function safeValue(value) {
  if (typeof value !== "string") return "";
  const text = value.trim();
  if (!text || text.length > 64) return "";
  if (text.includes("@") || text.includes("/") || text.includes(":") || text.includes("%")) {
    return "";
  }
  return SAFE_VALUE_PATTERN.test(text) ? text : "";
}

function safeScreen(value) {
  const screen = typeof value === "string" ? value.trim() : "";
  return SCREENS.has(screen) ? screen : FRAME_SCREEN;
}

function sanitizeParams(params) {
  if (!isRecord(params)) return {};
  const clean = {};
  const surface = safeValue(params.surface);
  if (SURFACES.has(surface)) clean.surface = surface;
  if (typeof params.screen === "string" && SCREENS.has(params.screen)) {
    clean.screen = params.screen;
  }
  const adCode = safeValue(params.ad_code);
  if (adCode) clean.ad_code = adCode;
  for (const key of UTM_KEYS) {
    const value = safeValue(params[key]);
    if (value) clean[key] = value;
  }
  return clean;
}

function callGtag(args) {
  const gtag = typeof window === "undefined" ? null : window.gtag;
  try {
    if (typeof gtag === "function") {
      gtag(...args);
      return true;
    }
    if (Array.isArray(gtag)) {
      gtag.push(args);
      return true;
    }
  } catch {
    // Analytics failures stay inside the frame.
  }
  return false;
}

function installGtagQueue() {
  try {
    window.dataLayer = window.dataLayer || [];
    if (typeof window.gtag !== "function") {
      const pending = Array.isArray(window.gtag) ? window.gtag : [];
      window.gtag = function gtag() {
        window.dataLayer.push(arguments);
      };
      for (const args of pending) window.dataLayer.push(args);
    }
  } catch {
    // Queue installation failures are isolated.
  }
}

function injectTag(measurementId) {
  try {
    const script = document.createElement("script");
    script.async = true;
    script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
    (document.head || document.documentElement).appendChild(script);
  } catch {
    // The queue still works if gtag.js cannot be injected.
  }
}

function postToParent(message) {
  try {
    window.parent.postMessage(message, safeOrigin());
  } catch {
    // A blocked parent frame is a silent no-op.
  }
}

function initialize(data) {
  if (initialized) return;
  const measurementId =
    typeof data.measurement_id === "string" ? data.measurement_id.trim().toUpperCase() : "";
  if (!MEASUREMENT_ID_PATTERN.test(measurementId)) return;
  installGtagQueue();
  injectTag(measurementId);
  callGtag(["js", new Date()]);
  callGtag(["set", frameDefaults()]);
  callGtag([
    "config",
    measurementId,
    {
      ...frameDefaults(),
      send_page_view: false,
      debug_mode: data.debug_mode === true,
    },
  ]);
  initialized = true;
  postToParent({ type: "ga4_ready" });
}

function forwardEvent(data) {
  const name = typeof data.name === "string" ? data.name : "";
  if (!EVENT_NAMES.has(name)) return;
  const params = sanitizeParams(data.params);
  const screen = safeScreen(params.screen);
  const eventParams = {
    ...params,
    page_location: pageLocation(screen),
    page_referrer: "",
  };
  if (name === "page_view") eventParams.page_title = screen;
  callGtag(["event", name, eventParams]);
}

function onMessage(event) {
  try {
    if (!event || event.origin !== safeOrigin()) return;
    if (typeof window === "undefined" || event.source !== window.parent) return;
    if (!isRecord(event.data)) return;
    if (event.data.type === "ga4_init") {
      initialize(event.data);
    } else if (event.data.type === "ga4_event" && initialized) {
      forwardEvent(event.data);
    }
  } catch {
    // Malformed messages never break the frame.
  }
}

try {
  window.addEventListener("message", onMessage);
} catch {
  // Without a message listener the frame stays a silent no-op.
}
