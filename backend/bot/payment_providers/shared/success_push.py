from __future__ import annotations

import logging
import re
from typing import Any

from bot.services.notification_channels import (
    NotificationChannelOutcome,
    NotificationOccurrence,
    fanout_push_occurrence,
    occurrence_id_for,
    plaintext_for_push,
)
from bot.services.user_notification_policy import UserNotificationCategory
from db.models import User

from .common import make_translator, sale_mode_base
from .success_messages import _HWID_DEVICE_MODES, is_traffic_sale_base

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S+")


def _push_safe_body(text: str) -> str:
    """Render push body without authentication secrets or subscription tokens.

    Success texts for gift purchases embed single-use gift links; config and
    install-share URLs must never reach a lock-screen preview. Strip raw URLs
    and keep the human-readable remainder with the dashboard action URL.
    """

    redacted = _URL_RE.sub("", text)
    return plaintext_for_push(redacted)


def _success_notification_category(sale_mode: str) -> UserNotificationCategory:
    base = sale_mode_base(sale_mode)
    if is_traffic_sale_base(base):
        return UserNotificationCategory.TRAFFIC
    if base in _HWID_DEVICE_MODES:
        return UserNotificationCategory.DEVICES
    return UserNotificationCategory.PAYMENTS


async def send_payment_success_push(
    *,
    settings: Any,
    i18n: Any,
    user: User | None,
    user_id: int,
    language: str,
    sale_mode: str,
    text: str,
    payment_db_id: int | None,
    log_prefix: str = "payment_providers",
) -> list[NotificationChannelOutcome] | None:
    """Fan out the success push for one payment without touching Telegram.

    Returns the per-destination outcomes so callers can observe failures; a
    failed destination releases its fanout claim and stays retryable on a
    repeated succeeded callback (see ``_retry_success_push_for_finalized`` in
    ``success.py``). Returns ``None`` when there is no user to notify.
    """

    if user is None:
        return None
    try:
        translator = make_translator(i18n, language)
        dashboard_url = str(settings.SUBSCRIPTION_MINI_APP_URL or "").strip() or None
        try:
            push_title = str(translator("email_payment_success_subject"))
        except Exception:
            push_title = "Payment received"
        if payment_db_id is not None:
            occurrence_id = occurrence_id_for("payment-succeeded", payment_db_id)
        else:
            occurrence_id = occurrence_id_for(
                "payment-succeeded", user_id, sale_mode, _push_safe_body(text)[:64]
            )
        return await fanout_push_occurrence(
            settings=settings,
            user=user,
            occurrence=NotificationOccurrence(
                occurrence_id=occurrence_id,
                category=_success_notification_category(sale_mode),
                user_id=int(getattr(user, "user_id", 0) or user_id),
                push_title=push_title,
                push_body=_push_safe_body(text),
                push_url=dashboard_url,
            ),
        )
    except Exception:
        logger.exception("%s: failed to fan out success push for user %s.", log_prefix, user_id)
        return None
