"""Accepted setup rewards and checkout guards for period activation."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.checkout_addons import CheckoutAddonGrants
from bot.services.referral_accruals import utc_date
from db.models import Payment, Subscription, User
from db.setup_reward_models import SetupReward

from ._typing import SubscriptionServiceMixinContract

logger = logging.getLogger(__name__)


async def pending_activation_rewards(
    session: AsyncSession, user_id: int, subscription: Subscription | None
) -> tuple[Sequence[SetupReward], datetime | None]:
    pending_reward_until = None
    pending_values: Sequence[SetupReward] = []
    if subscription is not None:
        pending_result = await session.scalars(
            select(SetupReward).where(
                SetupReward.user_id == user_id,
                SetupReward.subscription_id == subscription.subscription_id,
                SetupReward.accepted_days.is_not(None),
                SetupReward.target_end_date.is_not(None),
                SetupReward.state == "pending",
            )
        )
        pending_values = pending_result.all()
        if inspect.isawaitable(pending_values):
            pending_values = await pending_values
        pending_reward_until = max(
            (
                value
                for value in (utc_date(item.target_end_date) for item in pending_values)
                if value
            ),
            default=None,
        )
    return pending_values, pending_reward_until


def activation_checkout_matches(
    service: SubscriptionServiceMixinContract,
    payment: Payment | None,
    current_active_sub: Subscription | None,
    checkout_grants: CheckoutAddonGrants,
    pending_values: Sequence[SetupReward],
    *,
    payment_db_id: int,
    allow_subscription_end_change: bool,
) -> bool:
    if checkout_grants.active_context_present:
        from bot.payment_providers.shared.entitlement_context import (
            pending_setup_reward_explains_end_change,
            preflight_payment_entitlement,
        )

        current_subscription_id = (
            int(current_active_sub.subscription_id) if current_active_sub is not None else None
        )
        current_end_at = service._as_aware_utc(
            getattr(current_active_sub, "end_date", None) if current_active_sub else None
        )
        accepted_pending_reward_explains_change = pending_setup_reward_explains_end_change(
            payment,
            current_active_sub,
            list(pending_values) if current_active_sub is not None else [],
        )
        if accepted_pending_reward_explains_change:
            accepted_pending_reward_explains_change = preflight_payment_entitlement(
                payment,
                current_active_sub,
                allow_subscription_end_change=True,
            ).allowed
        if current_subscription_id != checkout_grants.active_subscription_id or (
            current_end_at != checkout_grants.active_end_at
            and not accepted_pending_reward_explains_change
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


def activation_reserved_until(
    db_user: User, pending_reward_until: datetime | None
) -> datetime | None:
    reserved_until = max(
        (
            value
            for value in (
                utc_date(getattr(db_user, "period_accrual_reserved_until", None)),
                pending_reward_until,
            )
            if value is not None
        ),
        default=None,
    )
    return reserved_until
