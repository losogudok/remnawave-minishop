# Website-to-VPN onboarding acceptance — 2026-11-05

Status: automated gates and available-browser acceptance completed. This is not a claim of
live VPN connection, OS push delivery, installed-iOS, Telegram WebView, or GA report acceptance.

## Revision and environment

- Source: local `main` after ONB-03/05/07/08/09/10/11 integration; browser checks ran on
  `ae41f004` (`v3.8.4+gae41f004` in the admin sidebar) and the final tree is `92bc3bb2`.
  The guide-media response fix was included and reverified.
- Dev stand: `127.0.0.1:18082`, rebuilt backend/frontend images from this revision.
- Browser: headless Chromium 156 (Playwright 1.64.1), desktop `1280×900` and mobile
  `390×844`/`375×812` CSS viewports. Mobile checks resize the viewport; they do not emulate
  iOS Safari, Android Chrome, or a Telegram WebView.
- Fixture note: the seeded admin public share needed `install_share_panel_short_uuid` repaired
  to the Panel `short_uuid` (`devadmin01`) before the signed-out page returned 200. This is the
  same seeded-identity fixture issue recorded in the Wave-1 report; it is not an ONB-02/03
  regression. The public guide API and page then rendered the uploaded gallery. The binding
  reverted on a later `/api/me`/sync pass, so this remains a dev-fixture limitation to fix in
  the seed/sync path.

## Automated gates

Run from the repository root with Python 3.12 (the prepared test environment):

- Full backend `pytest -q` on the final tree: **6,337 passed, 89 skipped, 705 subtests
  passed**, exit 0.
- Whole-repo `ruff check .`: passed.
- Whole-repo `ruff format --check .`: passed (1,569 files).
- Whole-backend `mypy`: **Success, 1,447 source files**.
- ONB-07 final security check: **APPROVED** by Sol; ONB-09 final scoped check approved the
  post-enqueue and preview fixes after the durable enqueue-failure outbox was added.
- Architecture gates: passed after splitting GA4/Web Push settings into dedicated modules
  (`settings.py` 944 lines, `admin_settings_manifest_fields.py` 986 lines).
- Frontend `npm run check`: passed, 0 svelte-check errors/warnings.
- Frontend `npm run test`: **1,714 tests / 218 files**.
- Frontend `npm run build`: passed; backend webapp/admin bundles regenerated.
- Docs: `npm run check:docs` and `npm run build:docs` passed (ONB-05/07/09 runs).
- OpenAPI, event catalog, settings manifest, migration snapshot and locale drift tests:
  passed.

## Browser evidence

| Area | Observed | Evidence |
| --- | --- | --- |
| ONB-01 login introduction | RU login page shows the localized introduction below branding; guest layout and login choices render without console errors. | `/tmp/opencode/qa-login.png` |
| ONB-02/03 authenticated instructions | `/install` lists Panel OS/client choices; selecting iOS → Happ shows the uploaded gallery image with RU caption. | `/tmp/opencode/qa-auth-install-ios.png` |
| ONB-03 admin galleries | Settings → Screenshots lists source/config, OS and client, marks the unavailable local source, shows an empty gallery, accepts a PNG upload, renders the thumbnail, saves RU/EN captions, and reloads with captions intact. The caption PATCH initially returned 500 (response serialized after session close); fixed in `ae41f004` and reverified. | `/tmp/opencode/qa-guide-media.png`, `qa-guide-uploads.png`, `qa-guide-captions.png` |
| ONB-02/03 public instructions | Signed-out `/s/<token>` at 390 px renders the guide; iOS → Happ shows the same gallery image and RU caption; no horizontal overflow (`scrollWidth == clientWidth == 390`) and no console errors. | `/tmp/opencode/qa-public-guide-mobile.png` |
| ONB-04 signals | GA disabled environment: no Google Analytics/GTM requests were observed across guest/authenticated navigation; no consent prompt. | Network listener in acceptance run |
| ONB-05 GA4 | Admin → Settings → Analytics exposes GA4 enable/measurement ID/API secret/debug fields. Plugin adapter/frame routes and event pipeline are covered by automated tests; no live GA property/DebugView was configured. | `docs/features/analytics.md`, `tests/unit/test_ga4funnel_plugin.py` |
| ONB-06/07 notifications | `/settings/notifications` shows the single browser-push card above the existing email/Telegram preferences. In headless Chromium the permission is blocked, so the card shows actionable denied guidance and the account-binding note; it does not offer a fake enable. Push enrollment/send behavior is covered by unit, route, worker, crypto and Playwright tests. | `/tmp/opencode/qa-notifications-mobile.png` |
| ONB-08 broadcast channels | Admin → Broadcast lists Telegram (disabled), Email (not configured) and **Web Push** as descriptor-driven channels; existing Telegram/email controls remain. | `/tmp/opencode/qa-broadcast.png` |
| ONB-09 push campaigns | Selecting Web Push renders the phone-style preview with title/body counters, plain-text/URL-button/image limits, per-device count copy and “OS display not confirmed” wording. The live title falls back to the configured `/minishop` once admin settings load. | `/tmp/opencode/qa-broadcast-push.png` |
| ONB-10 PWA infrastructure | `/manifest.webmanifest` 200 `application/manifest+json`, `no-cache`, name `/minishop`, `display: standalone`; `/sw.js` 200 JavaScript, `no-store`, `Service-Worker-Allowed: /`, length 9,088; one active root-scope worker controls the page. | Acceptance run output |
| ONB-11 installation UX | The post-sign-in offer appears once; dismissing it and reloading does not repeat it; Settings keeps “Install app”. The dialog gives browser-specific guidance (headless Chromium has no deferred prompt), states it is not the VPN client and does not enable notifications, and Escape closes it. | `/tmp/opencode/qa-install-dialog-mobile.png`, `qa-home.png` |

## Cross-feature limits

- No real VPN client import, live node connection or Panel-confirmed `first_connected` was
  performed. The connection funnel is covered by server/webhook/worker tests, not by a live
  client.
- No real Web Push service delivery, OS notification display, notification click, installed
  iOS Home Screen app, Safari push, Android device, or Telegram WebView push was verified.
  Headless Chromium reported browser permission blocked, which exercised the denied state.
- No live GA4 property, Measurement Protocol DebugView or report validation was performed.
  With GA disabled, the browser made zero Google requests and showed no consent gate.
- SMTP/Telegram broadcast delivery was not sent; both channels were disabled/unconfigured in
  the stand. Push enqueue/worker/callback paths use deterministic tests.
- The admin public-share fixture required a manual Panel `short_uuid` repair before the
  signed-out acceptance. The seed/sync path should preserve that binding without manual SQL;
  this is recorded as a dev-fixture follow-up, not a product claim.
- `/api/account/avatar` returned 500 (`KeyError: 'bot'`) in the local Telegram-disabled stand;
  this is unrelated to onboarding and was not changed.

## Cleanup

- Temporary VAPID test keys were removed from `.env.remnawave-dev` (restored from backup);
  the running containers retain the old environment until the next recreate.
- The uploaded test gallery row and its content-addressed file were removed from the dev stand.
- Temporary Playwright acceptance scripts were removed from `frontend/`; screenshots remain
  under `/tmp/opencode/` as evidence.
- No source edits were made by the browser pass except the guide-media response-session fix
  committed as `ae41f004`.
