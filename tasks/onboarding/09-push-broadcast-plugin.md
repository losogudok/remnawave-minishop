# ONB-09 — News and promotions through Web Push

Layer: plugin. Dependencies: ONB-07/ONB-08. Status: implemented and integrated into local `main`.

Acceptance evidence and limits: [implementation status](README.md#implementation-status--2026-11-05).

## Scope

Register Web Push as a delivery channel in existing admin broadcasts. Allow news
and promotions to all eligible users who enabled the approved notification preferences.

## Acceptance criteria

- The existing composer offers Web Push alongside Telegram/email, with push-only
  and mixed-channel campaigns; unavailable push has an understandable status.
- Enabling Web Push notifications also enables news/promotions. Do not require a
  separate marketing opt-in or reuse unrelated Telegram/email consent as push permission.
- Recipient counts and delivery honor that combined preference, valid endpoints,
  account eligibility, and the selected broadcast audience/channels. Send Web Push
  to every active subscribed device alongside other selected, enabled channels.
- Recheck opt-out and endpoint/account eligibility at send time for scheduled campaigns.
- Provide push-appropriate preview, bounded text, and supported click destinations;
  reject or explain incompatible Telegram-specific markup/actions.
- Reuse ONB-07 delivery, retries, and revocation; report user/device counts with
  explicit meanings, without claiming OS display or human viewing from enqueue success.
- Test push-only and mixed broadcasts, no-Telegram users, opt-out after scheduling,
  multiple devices, expired endpoints, partial failure, and retries.

## Source starting points

- `backend/bot/app/web/admin_api_impl/broadcast.py`
- `backend/db/dal/user_broadcast_dal.py`
- `backend/bot/services/admin_broadcast_worker.py`
- `backend/db/dal/broadcast_dal.py`
