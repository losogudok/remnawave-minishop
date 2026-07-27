from __future__ import annotations

import logging
from datetime import datetime

from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import payment_dal, tribute_dal
from db.models import Payment, TributeEntitlement, TributeWebhookEvent, User

from ..shared import PaymentSuccessRequest
from .config import (
    TRIBUTE_PROVIDER,
    TributePlanBinding,
    _as_utc,
    _binding_for_event,
    _event_order,
    _normalized_datetime,
    _subscriber_key,
)
from .models import TributeSubscriptionPayload, TributeWebhookEnvelope
from .webhook_runtime import TributeWebhookRuntime

logger = logging.getLogger(__name__)

_DUPLICATE_RECURRENCE_REASON = "duplicate_active_recurrence_manual_review"


class TributeSubscriptionWebhookMixin(TributeWebhookRuntime):
    async def _process_subscription_event(
        self,
        envelope: TributeWebhookEnvelope,
        payload: TributeSubscriptionPayload,
        fingerprint: str,
    ) -> web.Response:
        if payload.telegram_user_id is None:
            return web.json_response({"ok": True, "status": "ignored"})
        telegram_user_id = int(payload.telegram_user_id)
        async with self.async_session_factory() as session:
            db_user = await self._lock_user_for_telegram_id(session, telegram_user_id)
            if db_user is None:
                # A link is normally opened from this bot, so the user should
                # already exist. A retryable response preserves the short race
                # where Telegram delivered the webhook before /start committed.
                return web.json_response({"ok": False, "error": "user_not_found"}, status=404)
            # Everything below is keyed on the local account, not on the
            # Telegram ID Tribute sent.
            user_id = int(db_user.user_id)

            event, _created = await tribute_dal.ensure_webhook_event(
                session,
                {
                    "fingerprint": fingerprint,
                    "event_name": envelope.name,
                    "tribute_subscription_id": payload.subscription_id,
                    "tribute_period_id": payload.period_id,
                    "trb_user_id": _subscriber_key(payload),
                    "telegram_user_id": telegram_user_id,
                    "event_created_at": _as_utc(envelope.created_at),
                    "event_sent_at": _as_utc(envelope.sent_at),
                    "expires_at": _as_utc(payload.expires_at),
                    "price": payload.price,
                    "amount": payload.amount,
                    "currency": payload.currency,
                    "status": "processing",
                },
            )
            if event.status in {"processed", "ignored"}:
                await session.commit()
                return web.json_response({"ok": True, "status": event.status, "duplicate": True})

            entitlement = await tribute_dal.get_entitlement_for_update(
                session,
                tribute_subscription_id=payload.subscription_id,
                trb_user_id=_subscriber_key(payload),
            )
            if entitlement is not None and int(entitlement.telegram_user_id) != telegram_user_id:
                tribute_dal.mark_event_processed(
                    event,
                    status="ignored",
                    reason="subscriber_identity_mismatch",
                )
                await session.commit()
                return web.json_response({"ok": True, "status": "ignored"})

            stale_event = (
                entitlement is not None
                and str(entitlement.last_event_fingerprint) != fingerprint
                and self._is_stale_event(
                    entitlement,
                    event_name=envelope.name,
                    created_at=envelope.created_at,
                )
            )
            delayed_positive_event = (
                stale_event
                and entitlement is not None
                and self._is_delayed_positive_event(
                    entitlement,
                    event_name=envelope.name,
                )
            )
            if stale_event and not delayed_positive_event:
                tribute_dal.mark_event_processed(event, status="ignored", reason="stale_event")
                await session.commit()
                return web.json_response({"ok": True, "status": "ignored"})

            configured_binding = _binding_for_event(self.settings, payload)
            if delayed_positive_event:
                # The newer event is authoritative for recurrence state, but
                # this earlier positive event still represents a paid period
                # that must be granted exactly once. Use its entitlement's
                # frozen binding rather than mutable current configuration.
                assert entitlement is not None
                binding = self._binding_from_entitlement(entitlement, payload)
            else:
                binding = self._resolve_binding(
                    envelope.name,
                    payload,
                    entitlement,
                    configured_binding,
                )
            if binding is None:
                tribute_dal.mark_event_processed(event, status="ignored", reason="unknown_plan")
                await session.commit()
                logger.warning(
                    "Ignoring unmapped Tribute plan subscription=%s period=%s.",
                    payload.subscription_id,
                    payload.period_id,
                )
                return web.json_response({"ok": True, "status": "ignored"})

            if envelope.name != "cancelled_subscription":
                other_shop_order = await tribute_dal.get_other_active_shop_order_uuid(
                    session,
                    user_id=user_id,
                )
                other_creator_subscription = (
                    await tribute_dal.get_other_active_creator_subscription_id(
                        session,
                        user_id=user_id,
                        exclude_subscription_id=int(payload.subscription_id),
                    )
                )
                if other_shop_order is not None or other_creator_subscription is not None:
                    tribute_dal.mark_event_processed(
                        event,
                        status="ignored",
                        reason=_DUPLICATE_RECURRENCE_REASON,
                    )
                    await session.commit()
                    logger.error(
                        "Ignored conflicting Tribute Creator recurrence "
                        "subscription=%s user=%s active_shop_order=%s "
                        "active_creator_subscription=%s; manual review required.",
                        payload.subscription_id,
                        telegram_user_id,
                        other_shop_order,
                        other_creator_subscription,
                    )
                    return web.json_response(
                        {
                            "ok": True,
                            "status": "ignored",
                            "manual_review": True,
                        }
                    )

            if not delayed_positive_event:
                entitlement = await self._upsert_entitlement(
                    session,
                    entitlement=entitlement,
                    binding=binding,
                    envelope=envelope,
                    payload=payload,
                    fingerprint=fingerprint,
                    user_id=int(db_user.user_id),
                )

            if envelope.name == "cancelled_subscription":
                await self._disable_local_auto_renew(
                    session,
                    user_id=int(db_user.user_id),
                    tariff_key=binding.tariff_key,
                )
                tribute_dal.mark_event_processed(event)
                await session.commit()
                return web.json_response(
                    {
                        "ok": True,
                        "status": "pre_cancelled",
                        "expires_at": _normalized_datetime(payload.expires_at),
                    }
                )

            payment, payment_error = await self._finalize_positive_event_payment(
                session,
                db_user=db_user,
                event=event,
                envelope=envelope,
                payload=payload,
                binding=binding,
                fingerprint=fingerprint,
            )
            if payment_error is not None:
                return payment_error
            assert payment is not None

            if delayed_positive_event:
                # Finalization commits the paid entitlement. Re-assert the
                # provider's newer recurrence state in the follow-up
                # transaction and deliberately leave the entitlement's
                # latest-event fields/status untouched.
                assert entitlement is not None
                recurrence_status = str(entitlement.status or "")
                if recurrence_status == "pre_cancelled":
                    await self._disable_local_auto_renew(
                        session,
                        user_id=int(db_user.user_id),
                        tariff_key=binding.tariff_key,
                    )
                    processed_reason = "paid_period_after_cancellation"
                else:
                    await self._enable_local_auto_renew(
                        session,
                        user_id=int(db_user.user_id),
                        tariff_key=binding.tariff_key,
                    )
                    processed_reason = "paid_period_after_newer_positive"
                tribute_dal.mark_event_processed(
                    event,
                    reason=processed_reason,
                    payment_id=int(payment.payment_id),
                )
                await session.commit()
                return web.json_response(
                    {
                        "ok": True,
                        "status": "processed",
                        "payment_id": int(payment.payment_id),
                        "expires_at": _normalized_datetime(payload.expires_at),
                        "recurrence_status": recurrence_status,
                    }
                )

            await self._enable_local_auto_renew(
                session,
                user_id=int(db_user.user_id),
                tariff_key=binding.tariff_key,
            )
            tribute_dal.mark_event_processed(
                event,
                payment_id=int(payment.payment_id),
            )
            entitlement.status = "active"
            await session.commit()
            return web.json_response(
                {
                    "ok": True,
                    "status": "processed",
                    "payment_id": int(payment.payment_id),
                    "expires_at": _normalized_datetime(payload.expires_at),
                }
            )

    async def _finalize_positive_event_payment(
        self,
        session: AsyncSession,
        *,
        db_user: User,
        event: TributeWebhookEvent,
        envelope: TributeWebhookEnvelope,
        payload: TributeSubscriptionPayload,
        binding: TributePlanBinding,
        fingerprint: str,
    ) -> tuple[Payment | None, web.Response | None]:
        payment = await self._ensure_payment(
            session,
            event=event,
            envelope=envelope,
            payload=payload,
            binding=binding,
            fingerprint=fingerprint,
            user_id=int(db_user.user_id),
        )
        if str(payment.status or "").strip().lower() == "succeeded":
            return payment, None

        claimed = await payment_dal.claim_payment_finalization(
            session,
            int(payment.payment_id),
            provider_payment_id=fingerprint,
        )
        if claimed is None:
            payment = await payment_dal.get_payment_by_db_id(
                session,
                int(payment.payment_id),
                fresh=True,
            )
            if payment is None or str(payment.status or "").lower() != "succeeded":
                return None, web.json_response(
                    {"ok": False, "error": "activation_in_progress"},
                    status=503,
                )
            return payment, None

        payment = claimed
        outcome = await self._finalize_successful_payment(
            PaymentSuccessRequest(
                bot=self.bot,
                settings=self.settings,
                i18n=self.i18n,
                session=session,
                subscription_service=self.subscription_service,
                referral_service=self.referral_service,
                payment=payment,
                user_id=int(db_user.user_id),
                amount=float(payment.amount),
                currency=str(payment.currency),
                sale_mode=str(payment.sale_mode),
                months=binding.months,
                traffic_amount=None,
                provider_subscription=TRIBUTE_PROVIDER,
                provider_notification=TRIBUTE_PROVIDER,
                db_user=db_user,
                log_prefix="Tribute webhook",
                activation_extra_kwargs={"authoritative_end_at": _as_utc(payload.expires_at)},
                skip_referral_bonus=(
                    (payload.type or "regular") in {"trial", "gift"} or payload.price == 0
                ),
            )
        )
        if outcome is None:
            return None, web.json_response(
                {"ok": False, "error": "activation_failed"},
                status=500,
            )
        return payment, None

    @staticmethod
    def _is_stale_event(
        entitlement: TributeEntitlement,
        *,
        event_name: str,
        created_at: datetime,
    ) -> bool:
        current_key = (
            _as_utc(entitlement.last_event_created_at),
            _event_order(str(entitlement.last_event_name)),
        )
        incoming_key = (_as_utc(created_at), _event_order(event_name))
        return incoming_key <= current_key

    @staticmethod
    def _is_delayed_positive_event(
        entitlement: TributeEntitlement,
        *,
        event_name: str,
    ) -> bool:
        if event_name not in {"new_subscription", "renewed_subscription"}:
            return False
        latest_event = str(entitlement.last_event_name or "")
        status = str(entitlement.status or "")
        if latest_event == "cancelled_subscription":
            return status == "pre_cancelled"
        return (
            latest_event
            in {
                "new_subscription",
                "renewed_subscription",
            }
            and status == "active"
        )

    @staticmethod
    def _binding_from_entitlement(
        entitlement: TributeEntitlement,
        payload: TributeSubscriptionPayload,
    ) -> TributePlanBinding | None:
        if not entitlement.tariff_key or not entitlement.duration_months:
            return None
        return TributePlanBinding(
            tariff_key=str(entitlement.tariff_key),
            months=int(entitlement.duration_months),
            link="",
            subscription_id=int(entitlement.tribute_subscription_id),
            period_id=int(payload.period_id),
        )

    @staticmethod
    def _resolve_binding(
        event_name: str,
        payload: TributeSubscriptionPayload,
        entitlement: TributeEntitlement | None,
        configured: TributePlanBinding | None,
    ) -> TributePlanBinding | None:
        if entitlement is None:
            return configured
        if event_name == "new_subscription" and entitlement.status == "pre_cancelled":
            return configured
        if (
            event_name == "renewed_subscription"
            and str(entitlement.subscription_type or "") == "trial"
            and payload.type == "regular"
            and configured is not None
            and configured.tariff_key == entitlement.tariff_key
        ):
            return configured
        return TributeSubscriptionWebhookMixin._binding_from_entitlement(entitlement, payload)

    @staticmethod
    async def _upsert_entitlement(
        session: AsyncSession,
        *,
        entitlement: TributeEntitlement | None,
        binding: TributePlanBinding,
        envelope: TributeWebhookEnvelope,
        payload: TributeSubscriptionPayload,
        fingerprint: str,
        user_id: int,
    ) -> TributeEntitlement:
        active_until = _as_utc(payload.expires_at)
        if (
            entitlement is not None
            and envelope.name == "cancelled_subscription"
            and entitlement.active_until is not None
        ):
            active_until = max(_as_utc(entitlement.active_until), active_until)
        values = {
            "tribute_period_id": payload.period_id,
            "telegram_user_id": payload.telegram_user_id,
            "user_id": user_id,
            "tariff_key": binding.tariff_key,
            "duration_months": binding.months,
            "subscription_type": payload.type or "regular",
            "status": ("pre_cancelled" if envelope.name == "cancelled_subscription" else "active"),
            "active_until": active_until,
            "last_event_name": envelope.name,
            "last_event_created_at": _as_utc(envelope.created_at),
            "last_event_fingerprint": fingerprint,
        }
        if entitlement is None:
            return await tribute_dal.create_entitlement(
                session,
                {
                    "tribute_subscription_id": payload.subscription_id,
                    "trb_user_id": _subscriber_key(payload),
                    **values,
                },
            )
        for key, value in values.items():
            setattr(entitlement, key, value)
        await session.flush()
        return entitlement

    @staticmethod
    async def _ensure_payment(
        session: AsyncSession,
        *,
        event: TributeWebhookEvent,
        envelope: TributeWebhookEnvelope,
        payload: TributeSubscriptionPayload,
        binding: TributePlanBinding,
        fingerprint: str,
        user_id: int,
    ) -> Payment:
        if payload.telegram_user_id is None:
            raise ValueError("Tribute subscription payment is missing telegram_user_id")
        payment = await payment_dal.ensure_payment_with_provider_id(
            session,
            # The local account, never the Telegram ID Tribute sent: this is a
            # foreign key into users.user_id.
            user_id=int(user_id),
            amount=float(payload.price) / 100,
            currency=payload.currency,
            months=binding.months,
            description=f"Tribute: {payload.subscription_name}",
            provider=TRIBUTE_PROVIDER,
            provider_payment_id=fingerprint,
            sale_mode=f"subscription@{binding.tariff_key}",
            tariff_key=binding.tariff_key,
        )
        event.payment_id = int(payment.payment_id)
        await session.flush()
        return payment
