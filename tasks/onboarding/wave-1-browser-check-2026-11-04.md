# Wave-1 Core browser check — 2026-11-04

This is a historical report. Follow-ups at the top supersede the initial blocker
and verdict below. Later cross-feature acceptance is recorded in the
[2026-11-05 report](../../docs/development/onboarding-acceptance-2026-11-05.md).
Screenshot paths are machine-local evidence references, not repository assets.

**Dev-configuration follow-up, 2026-11-04:** Telegram is explicitly disabled
in the local env and dev env example (`TELEGRAM_ENABLED=False`), retaining
email/QA login. Configuration health now accepts HTTP loopback Mini App URLs
only in development/test; production, staging, remote hosts, credentials, and
malformed URLs retain the HTTPS error. Backend was rebuilt and recreated.
Fresh email authentication and `/api/admin/health?refresh=1` succeeded; the
browser admin panel no longer shows `bot_token_invalid` or
`mini_app_url_not_https`. The API still reports `panel_api_version_unknown`,
which the existing banner filters out separately.
57 targeted tests and 16 subtests, whole-backend mypy, formatting, architecture,
docs checks/build, and changed-file Ruff passed. The refreshed full run has
6,161 passed tests, 697 passed subtests, 13 skips, and the same pre-existing
settings-access hygiene failure; full Ruff retains the previously recorded
onboarding-signals union-order failure.

- Admin panel after dev-configuration repair: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mw2fa4h3-85860b32.png`

**Follow-up: public-guide blocker resolved on 2026-11-04.** The initial results
below record the original fixture failure. The repaired dev seed reads actual
Panel identities and binds valid share tokens to the current Panel `short_uuid`.
Anonymous page and guide-API checks now pass for both active seeded accounts
(200); the expired account remains unavailable (404). Reapplying the seed
preserves the repaired identities, tokens, and bindings. A signed-out browser
renders Linux desktop and Android mobile instructions, including OS/client
selection and download/import controls, without horizontal overflow or console
errors. Uploaded galleries still require ONB-03.

Repair validation: 5 PostgreSQL regression cases (UUID/numeric export, real seed
execution, share preservation/rotation, and missing-identity rollback), 8 existing
dev-stand unit tests, and 3 live anonymous public-guide cases passed. Compose
configuration, shell syntax, architecture, lockfiles, documentation checks/build,
Python formatting, changed-test Ruff, and whole-backend mypy (1,391 files) passed.
The full pytest run passed 6,158 tests and 681 subtests, skipped 13, and failed the
existing settings-access hygiene test. Full Ruff also reports the existing union
ordering violation in `tests/unit/test_onboarding_signals.py:48`; frontend checking
reports the missing `push` preference in
`frontend/src/lib/admin/stores/usersStoreNotificationPreferences.ts:43`. These
reported source files and their relevant types match `HEAD`; the repair does not
modify them. Aggregate quality gates remain red, so no all-green claim is made.
The frontend aggregate stopped at Svelte checking before its test/build/E2E steps.

- Public guide after repair, desktop: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mw2eq7rz-c46941a9.png`
- Public guide after repair, mobile viewport: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mw2esv3d-ef652721.png`

Revision: `b1400e79` (`v3.8.4+gb1400e79` in the running admin panel).
Stand: `http://127.0.0.1:18082`, Panel `3.4.7`.
Browser: T3 preview, Chromium 152 / Electron 44 on Linux; desktop and
375 × 667 CSS viewport. Mobile checks resize the viewport; they do not emulate
iOS, Android, their browsers, or a Telegram WebView.

Verdict: the available login, authenticated-guide, preferences, and PWA checks
passed. Public-guide acceptance is blocked by the seeded Panel identifiers.
Active integration delivery and installed-app behavior remain unverified.
This is a Wave-1 browser check, not completion of ONB-12.

## Results

| Task | Observed in the live browser | Limit |
| --- | --- | --- |
| ONB-01 | RU and EN login introductions render. Admin → Translations → `wa_login_intro` exposes both languages. Saving an EN change updates a separate guest browser without rebuilding. HTML-looking input renders literally; no injected element or handler runs. Saving an empty string removes the introduction while leaving login usable. Reset restores the base copy. Guest desktop/mobile layouts and the language menu remain usable. The seeded administrator signs in through the email-code dev shortcut. | No external Telegram/OAuth login, passkey, or newly added language tested. Only EN was temporarily edited; RU rendering and its editor were inspected. |
| ONB-02 | Authenticated `/api/admin/message/targets` returns `ok: true`, a missing `local:file` entry, and a separate populated Panel configuration. Panel catalog contains iOS, Linux, macOS, Android, Apple TV, Windows, Android TV, and stable client identifiers. Authenticated `/install` renders Panel instructions; OS selection changes clients and instruction content. Linux desktop and Android mobile views render with download/import controls. The OS menu fits on mobile, with no horizontal page overflow. | Screenshot-management plugin ONB-03 is absent: uploaded galleries, ordering, orphaned media, and provider-backed authenticated/public screenshots are not browser-tested. Public share navigation fails before rendering; see blocker below. |
| ONB-04 | Guest and authenticated bootstrap expose `config.onboardingSignals: []`. No third-party script/resource requests observed in the checked guest and authenticated flows. Login, guides, navigation, translations, and preference saving work with integrations absent. | No enabled signal adapter exists in this stand. Active signal sequencing, sanitization, provider failure, attribution ordering, and public-surface delivery require an integration fixture or the later plugin. No GA collection is claimed. |
| ONB-06 | Notification settings retain separate email/Telegram system and marketing controls on desktop/mobile. Turning marketing email off succeeds through `/api/account/notification-preferences`; `/api/me` reports only `marketing_email: false`, with the other preferences unchanged. Turning it back on restores all original values. The additive `push` preference is present in `/api/me`. | There is no Web Push provider or Web Push subscription UI yet. Browser preference persistence does not prove notification fanout, retries, idempotency, multi-device delivery, or actual email/Telegram/OS delivery. No notifications were sent. |
| ONB-10 | Manifest returns 200, `application/manifest+json`, `Cache-Control: no-cache`; identity, scope, and launch URL are `/`, with `display: standalone`. `/sw.js` returns 200, JavaScript MIME, `Cache-Control: no-store`, and `Service-Worker-Allowed: /`. Browser has one active root-scope worker, which controls the page. Manifest icons decode at 192×192, 512×512, and 180×180. Inspected cache contains static assets, with zero API/auth/share-page entries. | Installed standalone launch, HTTPS deployment, another-version worker upgrade, disabling/retirement, browser-specific installation, and push events/clicks were not tested. ONB-11 install button/post-login offer is not implemented and is not expected here. |

## Public-guide blocker: seeded Panel identities

1. Sign in using the seeded administrator on the canonical origin.
2. Obtain that subscription's `install_share_url` from `/api/me`.
3. Open it in a separate signed-out browser context.
4. Result: HTTP 503, body `Subscription unavailable`. The public page cannot render.

No share token is recorded in this report.

Backend logs show Panel returning HTTP 400, `Validation failed`, for lookup of
`00000000-0000-4000-8000-000000000001`. Live database inspection confirms:

| Seeded account | Mini Shop `users.panel_user_uuid` | Panel `users.id` |
| --- | --- | --- |
| `runes_admin` | `00000000-0000-4000-8000-000000000001` | `2` |
| `runes_active` | `00000000-0000-4000-8000-000000000002` | `3` |
| `runes_expired` | `00000000-0000-4000-8000-000000000003` | `4` |

`deploy/dev/seed-minishop.sql` writes UUID values. The public-access resolver
checks the linked Panel user afresh and returns unavailable when that lookup
fails. Authenticated guides can still resolve the Panel guide configuration,
so their successful rendering does not prove the public journey works.

Next: reconcile the seeded user/subscription Panel links through the supported
identity-sync path, verify the share binding, then repeat signed-out `/s/<token>`
and `/api/subscription-guides/public/<token>` checks. Verify that reapplying the
seed does not reintroduce invalid identifiers. No seed or subscription records
were repaired during this browser check. This does not establish an ONB-02
regression; public acceptance is blocked by the current fixture.

## Canonical-origin setup note

The preview initially opened `http://localhost:18082`. Its email-auth POST
returned 403. At the configured `http://127.0.0.1:18082` origin, the dev shortcut
completed email-code request and verification and opened the dashboard.
Use the configured origin for manual checks; do not disable origin protection.

## Manual walkthrough

1. Open `http://127.0.0.1:18082/` in a signed-out browser. Switch RU/EN and inspect
   the introduction and language menu at desktop and narrow widths.
2. Use **Dev login → Administrator**. Open `/admin/translations`, search
   `wa_login_intro`, expand **Mini App**, then the language. Save a temporary
   value, inspect it in a separate signed-out browser, test an empty value, then
   **Reset → Save** to restore the original state.
3. Open `/install`, choose OS and client, and inspect download/import actions.
   Screenshot upload management belongs to ONB-03 and is not present yet.
4. Open `/settings/notifications`, change one email/Telegram preference, reload
   to inspect persistence, and restore it. Web Push delivery belongs to ONB-07.
5. Inspect `/manifest.webmanifest`, `/sw.js`, browser service-worker registration,
   and Cache Storage. The installation action/post-login offer belongs to ONB-11.
6. Retest public guides after reconciling the seeded Panel links. Live VPN import
   and first connection need an actual VPN client and working node.

## Evidence

- RU guest mobile: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-localhost-mw2dxtsn-dca6fd51.png`
- Literal HTML-looking EN introduction: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mw2e0aem-0a4b51cd.png`
- Linux authenticated guide, desktop: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mw2e23xv-90387d8c.png`
- Android authenticated guide, mobile viewport: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mw2e2z0z-31205cf6.png`
- Notification preferences, mobile viewport: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mw2e4x9c-7604d45d.png`
- Notification preferences, desktop: `/home/normis/.t3/userdata/browser-artifacts/browser-screenshot-127-0-0-1-mw2e5oa5-d9670c1c.png`

## Cleanup and scope

The temporary EN locale override was reset and saved; the runtime mirror has no
`wa_login_intro` override. Marketing email was restored to its original enabled
value; other preferences are unchanged. No product source changes, database
fixture repairs, external sends, commits, pushes, or deployments were performed.
Browser-created local service-worker/cache state remains normal browser state.

The implementation gates reported with the batch were not rerun: this check
exercised the running Docker UI and browser-readable HTTP contracts. It does not
replace the unit/contract suite or device/external-service acceptance.
