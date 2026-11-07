# ONB-01 — Editable introduction on the login page

Layer: Core. Dependencies: none. Status: implemented and integrated into local `main`.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Add optional localized introduction text below the login-page logo/branding.
Reuse the existing translation editor and public bootstrap translation plumbing.
Leave the signed-in dashboard outside this task.

## Acceptance criteria

- An administrator can edit the introduction for each supported language without
  rebuilding the frontend; both RU and EN base keys exist.
- An explicitly empty introduction hides the block, with defined fallback behavior.
- Content renders as text, with no arbitrary script/HTML execution.
- Guest desktop/mobile layouts and supported login methods remain usable.
- Tests cover locale fallback, explicit hiding, escaping, and guest rendering.

## Source starting points

- `frontend/src/webapp/auth/AuthScreen.svelte`
- `frontend/src/admin/sections/TranslationsSection.svelte`
- `backend/bot/app/web/admin_api_impl/translations.py`
- `backend/bot/app/web/webapp/assets.py`
- `frontend/src/lib/webapp/i18n.ts`
