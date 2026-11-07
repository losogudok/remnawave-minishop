/* Minishop subscription Web App service worker.
 *
 * Single coordinated owner for the "/" scope: this is the only service worker
 * the core registers. Plugins must not register competing workers over "/"
 * (see docs/features/web-app.md "PWA" section for ownership, updates,
 * disabling, and removal).
 *
 * Caching policy (must stay in sync with deploy/docker/frontend/nginx.conf
 * immutable patterns and backend/bot/app/web/webapp/pwa.py):
 * - Cache-first ONLY for content-addressed, immutable same-origin assets
 *   (hashed bundles/chunks, digest-addressed brand assets, fonts, logos).
 *   A cache hit is byte-identical to a network fetch by construction, so an
 *   update can never strand a client on stale bytes.
 * - Everything else (navigations, /api/*, /auth/*, /s/* token share pages,
 *   HTML shells, user data) is network-only: the worker never calls
 *   respondWith for it, so nothing private is written to the cache.
 *
 * Cache Storage is shared across scopes: activation purges ONLY caches in the
 * core-owned "minishop-pwa-v" namespace (except the current generation), so
 * narrower-scope plugin workers never lose their caches.
 *
 * Updates are coordinated, never forced: the new worker installs into
 * "waiting" and does NOT call skipWaiting on its own. The open page observes
 * the waiting worker, sends {type: "SKIP_WAITING"}, and reloads once on
 * controllerchange (see pwa_registration_script in pwa.py). An open tab keeps
 * being served by the previous worker and its cache until it reloads onto the
 * new shell, so a lazy chunk import cannot be stranded mid-deploy.
 *
 * Push contract (explicit, no backend push sender in core):
 * - event.data must be JSON: {title, body?, url?, tag?, icon?, badge?,
 *   renotify?, silent?, data?}. Anything else is ignored silently.
 * - `title` is required and must be a non-empty string.
 * - `url` must be a same-origin path within this scope ("/..."); it defaults
 *   to the start URL "/". External origins and protocol-relative URLs are
 *   rejected. Servers must never put session tokens or secrets in `url`.
 * - notificationclick focuses an existing client at that URL or opens it.
 */

"use strict";

const CACHE_NAME = "__PWA_CACHE_NAME__";
const CACHE_NAME_PREFIX = "__PWA_CACHE_NAME_PREFIX__";
const START_URL = "/";

const HASHED_ENTRY_RE =
  /^\/subscription_webapp(_admin)?(\.min\.[0-9a-f]{8}|\.[0-9a-f]{8})\.(css|js)$/;
const CHUNK_RE = /^\/subscription_webapp(_admin)?\.[A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+\.js$/;
const FONT_RE = /^\/fonts\/TwemojiCountryFlags\.woff2$/;
const PROVIDER_LOGO_RE = /^\/provider-logos\/[A-Za-z0-9_-]+\.png$/;
const DIGEST_FAVICON_RE =
  /^\/webapp-favicon\/[0-9a-f]{16}\/(?:icon-(?:16|32|48|180|192|512)\.png|apple-touch-icon\.png|favicon\.(?:ico|svg))$/;
const UPLOADED_LOGO_RE =
  /^\/webapp-uploaded-logo\/logo-[0-9a-f]{16}\.(?:gif|ico|jpe?g|png|svg|webp)$/;

function isCacheableAsset(pathname) {
  return (
    HASHED_ENTRY_RE.test(pathname) ||
    CHUNK_RE.test(pathname) ||
    FONT_RE.test(pathname) ||
    PROVIDER_LOGO_RE.test(pathname) ||
    DIGEST_FAVICON_RE.test(pathname) ||
    UPLOADED_LOGO_RE.test(pathname)
  );
}

function isCoreOwnedCache(key) {
  return typeof key === "string" && key.startsWith(CACHE_NAME_PREFIX);
}

function isSameOrigin(url) {
  return url.origin === self.location.origin;
}

self.addEventListener("install", () => {
  // Intentionally no skipWaiting(): the previous worker keeps controlling open
  // tabs (and its cache generation stays intact) until the page explicitly
  // opts into the update via the "SKIP_WAITING" message below.
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    event.waitUntil(self.skipWaiting().catch(() => undefined));
  }
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => isCoreOwnedCache(key) && key !== CACHE_NAME)
            .map((key) => caches.delete(key).catch(() => undefined)),
        ),
      )
      .then(() => self.clients.claim())
      .catch(() => undefined),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }
  let url;
  try {
    url = new URL(request.url);
  } catch (err) {
    return;
  }
  if (!isSameOrigin(url)) {
    return;
  }
  if (request.mode === "navigate") {
    // Navigations (HTML shells, /install, /s/<token> share pages, auth) are
    // network-only: never serve or store them, so private pages and tokens
    // cannot leak into the cache.
    return;
  }
  if (!isCacheableAsset(url.pathname)) {
    return;
  }
  event.respondWith(
    caches
      .open(CACHE_NAME)
      .catch(() => null)
      .then((cache) => {
        if (!cache) {
          // Cache Storage itself is unavailable (quota, privacy mode, I/O
          // failure): the network is still healthy, so serve from it instead
          // of rejecting the page's asset request.
          return fetch(request);
        }
        return cache
          .match(request)
          .catch(() => undefined)
          .then(
            (cached) =>
              cached ||
              fetch(request).then((response) => {
                if (response && response.ok && response.type === "basic") {
                  cache.put(request, response.clone()).catch(() => undefined);
                }
                return response;
              }),
          );
      }),
  );
});

function sanitizePushUrl(rawUrl) {
  if (rawUrl === undefined || rawUrl === null || rawUrl === "") {
    return START_URL;
  }
  if (typeof rawUrl !== "string") {
    return null;
  }
  const trimmed = rawUrl.trim();
  if (!trimmed.startsWith("/") || trimmed.startsWith("//")) {
    return null;
  }
  try {
    const parsed = new URL(trimmed, self.location.origin);
    if (parsed.origin !== self.location.origin) {
      return null;
    }
    if (!parsed.pathname.startsWith("/")) {
      return null;
    }
    return parsed.pathname + parsed.search + parsed.hash;
  } catch (err) {
    return null;
  }
}

function sanitizePushIcon(rawIcon, fallback) {
  if (rawIcon === undefined || rawIcon === null || rawIcon === "") {
    return fallback;
  }
  if (typeof rawIcon !== "string") {
    return null;
  }
  const trimmed = rawIcon.trim();
  if (trimmed.startsWith("/")) {
    return sanitizePushUrl(trimmed);
  }
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol !== "https:") {
      return null;
    }
    return parsed.href;
  } catch (err) {
    return null;
  }
}

function parsePushPayload(event) {
  let data = null;
  try {
    data = event.data ? event.data.json() : null;
  } catch (err) {
    return null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    return null;
  }
  const title = typeof data.title === "string" ? data.title.trim() : "";
  if (!title) {
    return null;
  }
  const url = sanitizePushUrl(data.url);
  if (url === null) {
    return null;
  }
  const fallbackIcon = "/icon-192.png";
  const icon = sanitizePushIcon(data.icon, fallbackIcon);
  if (icon === null) {
    return null;
  }
  const badge =
    data.badge === undefined ? undefined : sanitizePushIcon(data.badge, fallbackIcon);
  if (badge === null) {
    return null;
  }
  const options = { body: "", icon, data: { url } };
  if (typeof data.body === "string" && data.body.trim()) {
    options.body = data.body.slice(0, 200);
  }
  if (typeof data.tag === "string" && data.tag) {
    options.tag = data.tag.slice(0, 64);
  }
  if (badge !== undefined) {
    options.badge = badge;
  }
  if (data.renotify === true) {
    options.renotify = true;
  }
  if (data.silent === true) {
    options.silent = true;
  }
  if (data.data !== undefined && data.data !== null && typeof data.data === "object") {
    options.data = { ...data.data, url };
  }
  return { title: title.slice(0, 120), options };
}

self.addEventListener("push", (event) => {
  const parsed = parsePushPayload(event);
  if (!parsed) {
    return;
  }
  event.waitUntil(
    self.registration.showNotification(parsed.title, parsed.options).catch(() => undefined),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const rawUrl =
    event.notification && event.notification.data && event.notification.data.url;
  const target = sanitizePushUrl(rawUrl) || START_URL;
  const targetUrl = new URL(target, self.location.origin).href;
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        for (const client of clients) {
          try {
            if (client.url === targetUrl && "focus" in client) {
              return client.focus();
            }
          } catch (err) {
            continue;
          }
        }
        if (self.clients.openWindow) {
          return self.clients.openWindow(target);
        }
        return null;
      })
      .catch(() => undefined),
  );
});
