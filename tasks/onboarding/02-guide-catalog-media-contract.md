# ONB-02 — Source-aware guide catalog and client media contract

Layer: Core. Dependencies: none. Status: implemented and integrated into local `main`.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Expose a typed admin catalog of all configured guide sources/configurations,
operating systems, and VPN clients. Add an additive contract for plugins to supply
ordered client screenshot galleries with localized captions to the existing
instruction renderer. Preserve Panel/local source selection.

## Acceptance criteria

- The catalog reflects actual configured clients, with explicit source identity;
  an empty local JSON override does not incorrectly imply an empty Panel catalog.
- Reuse/extract the projection behind `/api/admin/message/targets`; do not assume
  its current administrator-specific result represents every user's guide.
- Associate media by configuration, OS, and stable client identity, never list index.
  Reject ambiguous legacy identities and make removed/renamed clients visible.
- Images survive schema normalization and render through the shared guide screen;
  behavior without a provider is unchanged.
- Support authenticated and public subscription instructions. Authorize public
  contributions against the validated share context without making authenticated
  plugin runtime/assets public.
- Contract tests cover distinct configs reusing client IDs, legacy names/collisions,
  source precedence, missing sources, and relevant authenticated/public guide paths.

## Source starting points

- `backend/bot/app/web/admin_api_impl/message_targets.py`
- `backend/config/subscription_guides_config.py`
- `backend/bot/app/web/webapp/guide_document.py`
- `backend/bot/services/subscription_guides_resolution.py`
- `frontend/src/webapp/screens/InstallGuideScreen.svelte`
