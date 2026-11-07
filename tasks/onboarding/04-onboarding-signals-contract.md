# ONB-04 — Supported lifecycle and sanitized onboarding signals

Layer: Core. Dependencies: none. Status: implemented and integrated into local `main`.

## Scope

Provide an additive, vendor-neutral contract for deployment-enabled integrations to initialize
on guest/public/authenticated surfaces and observe allowlisted navigation/actions.
Define controlled asset/CSP contributions and browser-to-account binding.

## Acceptance criteria

- Integrations can observe the initial login visit and public instructions, as well
  as subsequent authenticated navigation; repeated mount/navigation does not duplicate signals.
- Signals distinguish instructions viewed, client download clicked, subscription
  import/copy clicked, registration, trial activation, and confirmed business outcomes.
- Expose safe screen/action names and explicit fields; raw URLs, subscription/share
  tokens, email addresses, Telegram identifiers, and arbitrary payloads are excluded.
- Coordinate acquisition metadata with existing advertising capture before its URL
  cleanup; preserve existing attribution behavior.
- When enabled by the administrator, initialize collection on the first visit
  without a visitor analytics consent prompt or opt-in gate.
- Disabling/removing an integration adds no third-party requests and cannot block
  authentication, checkout, instructions, or navigation.
- Define cleanup, trust, administrator enable/disable behavior, CSP validation, and supported lifecycle
  semantics; do not rely on scraping Core DOM or intercepting global fetch/history.
- Tests cover guest-to-auth transitions, public token URLs, repeated navigation,
  provider failure, disabled integration, and attribution ordering.

## Source starting points

- `frontend/src/App.svelte`
- `frontend/src/lib/webapp/advertising.ts`
- `frontend/src/lib/webapp/extensionHost.ts`
- `backend/bot/app/web/webapp/extension_runtime.py`
- `backend/bot/app/web/webapp/assets.py`
