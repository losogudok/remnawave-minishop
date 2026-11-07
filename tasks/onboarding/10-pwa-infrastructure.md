# ONB-10 — Shared PWA manifest and service-worker infrastructure

Layer: Core. Dependencies: none. Status: implemented and integrated into local `main`.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Provide deployment-safe install metadata and one coordinated service-worker owner
for the web app and optional push integration. Reuse existing branding/icons.

## Acceptance criteria

- Serve appropriate manifest, identity, icons, display mode, and safe launch URL over
  HTTPS; app identity/launch metadata contains no subscription token or session secret.
- Worker scope and headers work through the production Docker/nginx path, with
  documented ownership, registration, updates, disabling, and plugin removal.
- Push events/clicks integrate through an explicit supported contract; independent
  plugins do not register competing workers over the same scope.
- Do not cache private API responses, authenticated HTML, token-bearing share pages,
  or user-specific data by default. A worker update must not strand users on stale assets.
- Existing browser and Telegram Mini App operation remains usable without PWA/push.
- Verify manifest/worker responses, scope, MIME/cache headers, icon loading, update
  behavior, and installed launch in relevant browsers; document platform limits.

## Source starting points

- `backend/bot/app/web/templates/subscription_webapp.html`
- `backend/bot/app/web/webapp/assets.py`
- `deploy/docker/frontend/nginx.conf`
