# Website-to-VPN onboarding tasks

Historical status as of 2026-11-05: implementation integrated into local `main`; automated gates and
available-browser acceptance completed. Device and external-service acceptance
remain open; see the implementation status and linked acceptance report below.
The scope and task specifications preserve the original planning decisions.

## Confirmed scope

- Favor existing Plugin API contracts. Small, reusable Core additions are acceptable
  and should be independently reviewable for future upstream PRs.
- Add editable text below the logo on the login page only.
- In the admin panel, show VPN clients grouped by OS across all configured guide
  variants. Manage an optional ordered screenshot gallery for each client, with
  localized captions, and show it in authenticated and public subscription instructions.
  Do not assume this requires editing individual instruction steps.
- Measure the onboarding funnel in GA4, through confirmed first VPN connection.
  When analytics is enabled by the administrator, collect without a visitor consent
  prompt or opt-in gate. Keep analytics optional at deployment level.
- Offer Web Push for service updates and support replies, including users without
  Telegram. The same notification opt-in also enables news/promotions; do not add a
  separate marketing opt-in. Add Web Push to the existing broadcast composer.
  Deliver through every enabled channel and to every subscribed device, subject
  to existing eligibility/preferences and the selected broadcast audience/channels.
- Offer installation as a PWA, with platform-appropriate installation guidance:
  an action in settings and a one-time, non-blocking offer after sign-in, remembered
  separately per browser/device.
- The original planning request covered task creation only. Later implementation
  and acceptance are recorded in the status section below.

## Terms used by these tasks

- **Login page:** the unauthenticated screen containing branding and sign-in choices.
- **VPN client:** the external application used to import a subscription and connect.
- **Client screenshot:** an image attached to a VPN client within an OS/configuration.
- **Service notification:** an account, subscription, payment, device, or support update.
- **Marketing broadcast:** administrator-authored news or promotional content.
- **Delivery channel:** a transport such as Telegram, email, or Web Push.
- **Installed web app:** the Minishop PWA; installing it does not install a VPN client.
- **Confirmed first connection:** a first VPN connection confirmed by Panel data,
  distinct from clicking download, import, or connect instructions.

## Tasks and dependencies

1. [ONB-01 — Login introduction](01-login-introduction.md): independent Core change.
2. [ONB-02 — Guide catalog/media contract](02-guide-catalog-media-contract.md): Core prerequisite for ONB-03.
3. [ONB-03 — Screenshot administration](03-client-screenshot-plugin.md): plugin; depends on ONB-02.
4. [ONB-04 — Onboarding signals](04-onboarding-signals-contract.md): Core prerequisite for ONB-05.
5. [ONB-05 — GA4 funnel](05-ga4-funnel-plugin.md): plugin; depends on ONB-04.
6. [ONB-06 — Notification channels](06-notification-channel-contract.md): Core prerequisite for ONB-07 and ONB-08.
7. [ONB-07 — Web Push delivery](07-web-push-plugin.md): plugin; depends on ONB-06 and ONB-10.
8. [ONB-08 — Broadcast channels](08-broadcast-channel-contract.md): Core; depends on ONB-06.
9. [ONB-09 — Push campaigns](09-push-broadcast-plugin.md): plugin; depends on ONB-07 and ONB-08.
10. [ONB-10 — PWA infrastructure](10-pwa-infrastructure.md): Core; independent of notification policy.
11. [ONB-11 — Installation experience](11-pwa-install-experience.md): depends on ONB-10; use supported plugin placements where sufficient.
12. [ONB-12 — Journey acceptance](12-journey-acceptance.md): depends on the preceding feature tasks.

## Resolved product decisions

- D1: Optional ordered screenshot galleries per OS/client, with localized captions.
- D2: Manage all configured guide variants and display images in both authenticated
  instructions and public subscription guides.
- D3: No visitor analytics consent prompt or opt-in gate. Collection starts when
  the administrator has enabled/configured analytics.
- D4: A single Web Push notification opt-in covers service/support and news/promotions.
  Browser permission is still required; existing Telegram/email preferences are preserved.
- D5: Deliver through all enabled channels and to all subscribed devices. An enabled
  channel is not merely a fallback for another channel. For broadcasts, also honor
  the administrator's selected audience/channels.
- D6: Installation is accessible from settings and offered once after sign-in.
- D7: Remember the post-sign-in installation offer per browser/device, not globally
  per account. Keep the settings action available after the offer is dismissed.

## Execution and verification requirements

- Follow the resolved scope above; dependencies describe implementation order,
  not unresolved product decisions.
- Keep Core extension contracts additive; plugin removal must leave Core usable.
- Preserve envelope/event/plugin contracts, append-only migrations, runes-only
  TypeScript frontend, and RU/EN localization under [CONTRIBUTING.md](../../CONTRIBUTING.md).
- Reuse existing entity/action components and inspect two actual consumers before UI
  changes. Verify affected desktop/mobile views, open menus, and dialogs.
- Generate affected OpenAPI, frontend types, event docs, and settings manifests;
  never edit their generated output manually.
- Run the changed-zone validation matrix for implementation; cross-layer changes
  require the full relevant gates. Update the dev changelog for noteworthy Core changes.
- Record browser/device and external-service verification separately from host tests.

## Evidence baseline

Read-only source inspection at commit `7350535fd12fed47125e722a23bef8d0f49e61c7`.
[The feasibility note](../../PLUGIN-ANALYTICS-FEASIBILITY.md) contains historical
dirty-fork sections; their absent implementation files are not current dependencies.
Contract limits were rechecked during planning. This baseline predates the
implementation and acceptance recorded below.

## Implementation status — 2026-11-05

All twelve tasks are implemented and integrated into local `main` (no push/deploy).
Final tree: `e8d63dc3`; acceptance report:
[`docs/development/onboarding-acceptance-2026-11-05.md`](../../docs/development/onboarding-acceptance-2026-11-05.md).

| Task | Delivery |
| --- | --- |
| ONB-01 | Editable login introduction (earlier wave) |
| ONB-02 | Source-aware guide catalog and media contract (earlier wave) |
| ONB-03 | Built-in `guide_media` plugin, admin Screenshots tab, public/auth galleries |
| ONB-04 | Sanitized onboarding signals contract (earlier wave) |
| ONB-05 | Built-in `ga4funnel` plugin, isolated tag frame, authoritative server events |
| ONB-06 | Extensible notification-channel contract (earlier wave) |
| ONB-07 | Built-in `web_push` plugin, enrollment/device UI, encrypted delivery worker |
| ONB-08 | Broadcast channel-provider contract, endpoint accounting, dynamic composer |
| ONB-09 | Web Push broadcast channel, push preview, durable callback outbox |
| ONB-10 | PWA manifest/service worker (earlier wave) |
| ONB-11 | Settings install action and one-time post-sign-in offer |
| ONB-12 | Automated gates + available-browser acceptance and documented limits |
