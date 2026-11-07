# ONB-05 — Optional per-installation GA4 onboarding funnel

Layer: plugin. Dependencies: ONB-04. Status: implemented and integrated into local `main`.

## Scope

Collect the agreed funnel: visit, registration, trial/purchase, instructions viewed,
client-download/import clicks, and confirmed first VPN connection. Own GA settings,
browser context binding, server delivery, and plugin persistence.

## Acceptance criteria

- Use the deployment owner's GA4 property; empty/disabled configuration sends no GA data.
- When configured/enabled by the administrator, collect browser and server funnel
  events without a visitor consent prompt or opt-in gate. Include the first visit;
  do not wait for sign-in. Disabling the integration stops new collection/delivery.
- Use actual tag-generated client context for joined browser/server reporting;
  never fabricate a client ID from an account or Telegram identifier.
- Derive registration, trial activation, and payment from authoritative outcomes.
  Applying an activation code or clicking checkout does not count as fulfillment.
- Resolve and deduplicate Panel `user.first_connected` notifications, distinguish
  occurrence time from delivery time, and reconcile when reliable capture is required.
- Distinguish in-process callback capture from durable delivery. Specify recovery
  for missed/early callbacks, delayed connection, duplicate webhooks, and restarts.
- Keep Measurement Protocol secrets server-side; allowlist every outbound field.
- Tests prove event meanings, deduplication, account switching, first-visit collection,
  administrator enable/disable behavior, and failure isolation. Separately record
  GA DebugView/report validation.

## Source starting points

- `backend/bot/infra/event_payloads.py`
- `backend/bot/services/panel_webhook_service.py`
- `backend/bot/plugins/extensions/jobs.py`
- `backend/bot/services/subscription_service_impl/trial.py`
- `backend/bot/payment_providers/shared/success.py`
