# ONB-11 — Platform-aware web-app installation button

Layer: existing plugin UI where sufficient; small Core additions where necessary.
Dependencies: ONB-10. Status: implemented and integrated into local `main`.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Offer installation of Minishop as a standalone web app on supported phone/desktop
platforms. Distinguish it clearly from downloading the external VPN client.

## Acceptance criteria

- Provide an installation action in settings and a one-time, non-blocking offer
  after sign-in. Persist whether it has been shown locally per browser/device;
  re-renders, logout, account switching, and subsequent sign-ins on that browser
  do not repeat it. A different browser/device has its own offer state.
- Keep the settings installation action available after the one-time offer is
  dismissed. Document that clearing site storage can reset its local shown state.
- Present the one-time offer as app UI; open the OS/browser installation prompt only
  on the user's explicit install action.
- Installation never becomes a prerequisite to acquiring a subscription, reading
  instructions, or connecting to the VPN.
- Use the browser prompt only when available; otherwise show accurate localized
  platform guidance, including iOS Add to Home Screen and browser handoff where needed.
- Handle already-installed, dismissed, unsupported, and embedded-browser states.
- A request to enable push explains any platform-specific installation prerequisite;
  installing the PWA alone does not enable notifications or grant browser permission.
- Installation analytics distinguish button clicks, accepted prompts, observable
  install events, and unavailable confirmation; none counts as a VPN connection.
- Verify desktop/mobile layouts, keyboard accessibility, dismissal, subsequent visits,
  and standalone launch. Reuse existing UI and satisfy base RU/EN localization.

## Source starting points

- `docs/development/plugin-ui-composition.md`
- `frontend/src/webapp/AuthenticatedScreens.svelte`
- `frontend/src/webapp/auth/AuthScreen.svelte`
