"""Checkout context guards for period activation."""

from __future__ import annotations

import logging
from datetime import datetime

from bot.services.checkout_addons import CheckoutAddonGrants
from bot.services.referral_accruals import utc_date
from db.models import Payment, Subscription, User

from ._typing import SubscriptionServiceMixinContract

logger = logging.getLogger(__name__)


def activation_checkout_matches(
    service: SubscriptionServiceMixinContract,
    payment: Payment | None,
    current_active_sub: Subscription | None,
    checkout_grants: CheckoutAddonGrants,
    *,
    payment_db_id: int,
    allow_subscription_end_change: bool,
) -> bool:
    if checkout_grants.active_context_present:
        from bot.payment_providers.shared.entitlement_context import (
            preflight_payment_entitlement,
        )

        current_subscription_id = (
            int(current_active_sub.subscription_id) if current_active_sub is not None else None
        )
        current_end_at = service._as_aware_utc(
            getattr(current_active_sub, "end_date", None) if current_active_sub else None
        )
        if current_subscription_id != checkout_grants.active_subscription_id or (
            current_end_at != checkout_grants.active_end_at
            and not (
                allow_subscription_end_change
                and preflight_payment_entitlement(
                    payment, current_active_sub, allow_subscription_end_change=True
                ).allowed
            )
        ):
            logger.error(
                "Checkout quote became stale for payment %s: subscription=%s/%s end=%s/%s",
                payment_db_id,
                checkout_grants.active_subscription_id,
                current_subscription_id,
                checkout_grants.active_end_at,
                current_end_at,
            )
            return False
    return True


def activation_reserved_until(db_user: User) -> datetime | None:
    return utc_date(getattr(db_user, "period_accrual_reserved_until", None))
