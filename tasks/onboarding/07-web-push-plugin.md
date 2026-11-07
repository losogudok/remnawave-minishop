# ONB-07 — Web Push subscriptions and service/support delivery

Layer: plugin. Dependencies: ONB-06/ONB-10. Status: implemented and integrated into local `main`.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Own browser subscription enrollment/preferences, endpoint persistence, credentials,
and background delivery. Connect the notification provider to the shared service worker.

## Acceptance criteria

- Request browser permission only through an explicit user action and explain denied,
  unsupported, and installation-required states without blocking VPN onboarding.
- Associate endpoints with authenticated accounts and devices; handle logout,
  account switching, revocation, expired endpoints, and banned/deleted/merged accounts.
- The notification enable action covers service/support updates and news/promotions;
  describe that scope in localized UI copy without an additional marketing opt-in.
  Disabling notifications stops both categories for the affected subscription/preference.
- Deliver to every active subscribed device alongside all other enabled, eligible
  channels. Users without Telegram remain eligible.
- Provide device-specific disable controls; revoking or logging out one device does
  not revoke other devices. Recheck endpoint ownership before queued delivery so
  account switching cannot deliver the previous account's notifications.
- Keep sender credentials server-side and endpoints private; enrollment and send
  paths enforce authorization, bounded input, and controlled outbound delivery.
- Persist delivery jobs with bounded transient retry/backoff, deduplication,
  restart recovery, and terminal endpoint removal.
- Notification clicks use approved same-origin destinations and require authentication
  for protected content. Lock-screen previews contain no credentials or subscription tokens.
- Verify supported desktop/mobile browsers separately, including installed iOS web apps;
  unit tests alone do not prove background OS delivery or exact-once display.

## Source starting points

- `backend/bot/plugins/spec.py`
- `frontend/src/lib/webapp/stores/accountStore.svelte.ts`
- `backend/bot/app/web/webapp/auth_oauth.py`
- `docs/development/plugin-ui-composition.md`
