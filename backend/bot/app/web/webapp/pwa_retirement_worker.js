/* Minishop PWA retirement worker.
 *
 * Served at /sw.js while PWA is disabled (PWA_ENABLED=False or
 * WEBAPP_ENABLED=False). Its only job is to retire an already-installed
 * core registration: take over immediately, delete ONLY the core-owned
 * "minishop-pwa-v" caches, then unregister so the worker stops intercepting
 * fetches and push events. Cache Storage is shared across scopes, so caches
 * owned by narrower-scope plugin workers are left untouched.
 *
 * No fetch/push/notificationclick handlers: once installed, requests flow to
 * the network directly. Controlled pages stay controlled until their next
 * load; the registration script reloads once on controllerchange, and every
 * fresh navigation revalidates /sw.js (no-store), so retirement propagates
 * without user action.
 */

"use strict";

const CACHE_NAME_PREFIX = "__PWA_CACHE_NAME_PREFIX__";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting().catch(() => undefined));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter(
              (key) => typeof key === "string" && key.startsWith(CACHE_NAME_PREFIX),
            )
            .map((key) => caches.delete(key).catch(() => undefined)),
        ),
      )
      .then(() => self.registration.unregister())
      .catch(() => undefined),
  );
});
