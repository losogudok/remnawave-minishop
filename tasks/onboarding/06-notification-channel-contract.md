# ONB-06 — Extensible user notification channels

Layer: Core. Dependencies: none. Status: implemented and integrated into local `main`.

## Scope

Inventory existing customer service/support notifications and introduce an additive
channel-provider/notification-intent contract. Preserve Core eligibility, category,
ban, availability, and preference policy rather than reimplementing it in a plugin.

## Acceptance criteria

- Inventory payments, subscription/reminder/expiry, traffic/limits, device activity,
  referral updates, and support replies; distinguish user notices from admin/log messages.
- Include direct-send paths, particularly reminders and support, instead of assuming
  all Telegram messages originate in one domain-event stream.
- Produce one business occurrence with channel-appropriate content/actions; push
  eligibility never depends on successful Telegram delivery or Telegram ownership.
- Define channel registration, availability, preferences, outcome reporting, and
  behavior when a provider is absent. Unknown channels never fall through to email.
- Use one Web Push preference for service/support and news/promotions; enabling
  notifications enables both. Preserve existing Telegram/email category preferences.
- Fan out service/support occurrences to every enabled, eligible channel and every
  subscribed device. Preserve existing Telegram/email category eligibility; do not
  suppress one channel because another channel succeeded or failed.
- Exclude authentication secrets and admin-only notices from automatic push mirroring.
- Define idempotent occurrence identity and reliable enqueue boundaries across backend
  and worker. Deduplicate at the occurrence/channel/destination level so independent
  channel or device deliveries do not suppress each other. Tests cover direct paths,
  duplicates, independent channel failure, unavailable providers, and policy.

## Source starting points

- `backend/bot/services/user_notification_policy.py`
- `backend/bot/services/user_notification_preferences.py`
- `backend/bot/services/event_reactions.py`
- `backend/bot/services/subscription_lifecycle_notifications.py`
- `backend/bot/services/notification_support.py`
