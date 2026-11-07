# ONB-12 — Verify the complete website-to-VPN journey

Layer: cross-feature acceptance. Dependencies: ONB-01 through ONB-11.
Status: automated and available-browser acceptance completed; device and external-service checks remain open.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Demonstrate that a first-time visitor can understand the service, get a subscription,
find their VPN client, import the subscription, and connect. Verify optional PWA,
push, and analytics behavior without turning them into onboarding blockers.

## Acceptance criteria

- Run guest-to-registration, trial and paid subscription, OS/client selection,
  screenshot display, download/import, and first-connection scenarios.
- Include guest/public guide, Telegram Mini App, regular browser, and installed PWA
  surfaces in the agreed platform matrix; identify genuinely unsupported combinations.
- Verify notification enable/disable behavior, service/support and news/promotions
  enabled by the same opt-in, multi-device/account switching, and authenticated
  notification-click destinations.
- Verify independent delivery to all enabled channels/subscribed devices, device-specific
  revocation, and partial failures. For campaigns, preserve selected audience/channels.
- Verify GA collection starts on the initial visit when configured, without an
  analytics consent prompt. Validate funnel events against authoritative outcomes;
  notification denial, analytics blockers, disabled integrations, or GA/push outages
  never break the customer journey or create false connection counts.
- Verify settings installation and the one-time post-sign-in offer, including its
  persistence per browser/device across sign-ins/account switches, independence on
  another device, dismissal, already-installed state, and lack of blocking prompts.
- Separate automated checks from real client installation, subscription import,
  live VPN connection, OS push delivery, and GA report inspection.
- Attach evidence and remaining limits to the implementation PRs. Complete relevant
  CONTRIBUTING gates and changelog updates before declaring implementation ready.
