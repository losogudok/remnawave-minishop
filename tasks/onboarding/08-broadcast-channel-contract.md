# ONB-08 — Extensible channels in the existing broadcast workflow

Layer: Core. Dependencies: ONB-06. Status: implemented and integrated into local `main`.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Extend the existing admin broadcasts with an additive delivery-channel contract,
reusing composer, audiences, localization, scheduling, persistence, and history.

## Acceptance criteria

- Registered channels expose availability, supported content/actions, recipient
  eligibility, delivery dispatch, and meaningful progress/failure outcomes.
- Replace hardcoded Telegram/email branching with explicit channel handling without
  changing the behavior of existing campaigns or persisted delivery records.
- Preserve scheduled jobs, restart recovery, cancellation behavior, and history.
- Define user-level versus endpoint-level accounting; existing uniqueness by
  broadcast/user/channel must not lose deliveries to multiple push devices.
- Fan out to all administrator-selected channels enabled for each eligible recipient;
  channel/destination failure does not suppress other selected channel deliveries.
- Tests cover plugin absence, unknown channels, mixed campaigns, serialization,
  historical Telegram/email records, and interrupted delivery recovery.

## Source starting points

- `frontend/src/admin/sections/BroadcastSection.svelte`
- `frontend/src/lib/admin/stores/broadcastStore.svelte.ts`
- `backend/bot/services/message_composition.py`
- `backend/bot/services/admin_broadcast_delivery.py`
- `backend/db/broadcast_models.py`
