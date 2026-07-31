from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from aiohttp import web

from db.dal import payment_dal, subscription_dal, tribute_dal
from db.models import Payment

from ..shared import (
    EntitlementContextError,
    PaymentSuccessRequest,
    payment_units_for_activation,
    preflight_payment_entitlement,
    snapshot_current_entitlement_context,
)
from .config import TRIBUTE_PROVIDER, _as_utc, _product_binding_for_event
from .models import TributeDigitalProductPayload, TributeDigitalProductRefundPayload
from .webhook_runtime import TributeWebhookRuntime

logger = logging.getLogger(__name__)


class TributeProductWebhookMixin(TributeWebhookRuntime):
    @staticmethod
    def _product_identity_mismatch(purchase: Any, payload: Any) -> str | None:
        immutable_values = (
            ("product_id", int(purchase.tribute_product_id), int(payload.product_id)),
            (
                "transaction_id",
                int(purchase.tribute_transaction_id),
                int(payload.transaction_id),
            ),
            ("amount", int(purchase.amount), int(payload.amount)),
            ("currency", str(purchase.currency).upper(), str(payload.currency).upper()),
        )
        for field, stored, incoming in immutable_values:
            if stored != incoming:
                return f"{field}_mismatch"
        stored_telegram_id = getattr(purchase, "telegram_user_id", None)
        incoming_telegram_id = getattr(payload, "telegram_user_id", None)
        if (
            stored_telegram_id is not None
            and incoming_telegram_id is not None
            and int(stored_telegram_id) != int(incoming_telegram_id)
        ):
            return "telegram_user_id_mismatch"
        return None

    @staticmethod
    def _mark_product_fulfilled(purchase: Any, payment_id: int) -> None:
        purchase.status = "fulfilled"
        purchase.status_reason = None
        purchase.payment_id = int(payment_id)
        purchase.fulfilled_at = datetime.now(UTC)

    async def _process_digital_product_purchase(
        self,
        payload: TributeDigitalProductPayload,
    ) -> web.Response:
        binding = _product_binding_for_event(self.settings, payload.product_id)
        if binding is None:
            logger.warning(
                "Ignoring unmapped Tribute Digital Product %s.",
                payload.product_id,
            )
            return web.json_response({"ok": True, "status": "ignored", "reason": "unknown_product"})
        if payload.telegram_user_id is None:
            return web.json_response(
                {
                    "ok": True,
                    "status": "ignored",
                    "reason": "missing_telegram_identity",
                }
            )

        async with self.async_session_factory() as session:
            db_user = await self._lock_user_for_telegram_id(session, payload.telegram_user_id)
            if db_user is None:
                return web.json_response(
                    {"ok": False, "error": "user_not_found"},
                    status=404,
                )

            purchase, _created = await tribute_dal.ensure_product_purchase(
                session,
                {
                    "tribute_purchase_id": payload.purchase_id,
                    "tribute_transaction_id": payload.transaction_id,
                    "tribute_product_id": payload.product_id,
                    "trb_user_id": payload.trb_user_id,
                    "telegram_user_id": payload.telegram_user_id,
                    "user_id": int(db_user.user_id),
                    "tariff_key": binding.tariff_key,
                    "sale_mode": binding.sale_mode,
                    "units": binding.units,
                    "amount": payload.amount,
                    "currency": payload.currency,
                    "status": "processing",
                    "purchase_created_at": _as_utc(payload.purchase_created_at),
                },
            )
            mismatch = self._product_identity_mismatch(purchase, payload)
            if mismatch is not None:
                purchase.status = "quarantined"
                purchase.status_reason = mismatch
                await session.commit()
                logger.error(
                    "Quarantining mismatched Tribute Digital Product purchase %s: %s.",
                    payload.purchase_id,
                    mismatch,
                )
                return web.json_response({"ok": True, "status": "quarantined", "reason": mismatch})
            if purchase.status == "refunded":
                await session.commit()
                return web.json_response(
                    {"ok": True, "status": "ignored", "reason": "already_refunded"}
                )
            if purchase.status == "quarantined":
                await session.commit()
                return web.json_response(
                    {
                        "ok": True,
                        "status": "quarantined",
                        "reason": purchase.status_reason,
                        "duplicate": True,
                    }
                )
            if purchase.status == "fulfilled":
                await session.commit()
                return web.json_response({"ok": True, "status": "processed", "duplicate": True})

            payment: Payment | None = None
            if purchase.payment_id is not None:
                payment = await payment_dal.get_payment_by_db_id(
                    session,
                    int(purchase.payment_id),
                    fresh=True,
                )
                if payment is not None and str(payment.status or "").lower() == "succeeded":
                    self._mark_product_fulfilled(purchase, int(payment.payment_id))
                    await session.commit()
                    return web.json_response({"ok": True, "status": "processed", "duplicate": True})

            effective_sale_mode = str(purchase.sale_mode or binding.sale_mode)
            effective_tariff_key = str(purchase.tariff_key or binding.tariff_key)
            effective_units = float(purchase.units or binding.units)
            if payment is not None:
                active_subscription = await subscription_dal.get_active_subscription_by_user_id(
                    session,
                    int(db_user.user_id),
                )
                preflight = preflight_payment_entitlement(payment, active_subscription)
                if not preflight.allowed:
                    purchase.status = "quarantined"
                    purchase.status_reason = preflight.reason
                    await payment_dal.update_payment_status_by_db_id(
                        session,
                        int(payment.payment_id),
                        "activation_failed",
                    )
                    await session.commit()
                    return web.json_response(
                        {
                            "ok": True,
                            "status": "quarantined",
                            "reason": preflight.reason,
                        }
                    )
                entitlement_context_snapshot = getattr(
                    payment,
                    "entitlement_context_snapshot",
                    None,
                )
            else:
                try:
                    entitlement_context_snapshot = await snapshot_current_entitlement_context(
                        session,
                        user_id=int(db_user.user_id),
                        sale_mode=effective_sale_mode,
                    )
                except EntitlementContextError as exc:
                    purchase.status = "quarantined"
                    purchase.status_reason = str(exc)
                    await session.commit()
                    logger.warning(
                        "Quarantining Tribute Digital Product purchase %s: %s.",
                        payload.purchase_id,
                        exc,
                    )
                    return web.json_response(
                        {
                            "ok": True,
                            "status": "quarantined",
                            "reason": str(exc),
                        }
                    )
            payment = await payment_dal.ensure_payment_with_provider_id(
                session,
                user_id=int(db_user.user_id),
                amount=float(purchase.amount) / 100,
                currency=str(purchase.currency),
                months=max(1, int(effective_units)),
                description=f"Tribute: {payload.product_name}",
                provider=TRIBUTE_PROVIDER,
                provider_payment_id=f"digital_product:{payload.purchase_id}",
                sale_mode=effective_sale_mode,
                tariff_key=effective_tariff_key,
                purchased_gb=effective_units,
                entitlement_context_snapshot=entitlement_context_snapshot,
            )
            purchase.payment_id = int(payment.payment_id)
            await session.flush()

            if str(payment.status or "").strip().lower() == "succeeded":
                self._mark_product_fulfilled(purchase, int(payment.payment_id))
                await session.commit()
                return web.json_response({"ok": True, "status": "processed", "duplicate": True})

            provider_payment_id = f"digital_product:{payload.purchase_id}"
            claimed = await payment_dal.claim_payment_finalization(
                session,
                int(payment.payment_id),
                provider_payment_id=provider_payment_id,
            )
            if claimed is None:
                payment = await payment_dal.get_payment_by_db_id(
                    session,
                    int(payment.payment_id),
                    fresh=True,
                )
                if payment is None or str(payment.status or "").lower() != "succeeded":
                    return web.json_response(
                        {"ok": False, "error": "activation_in_progress"},
                        status=503,
                    )
            else:
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
                        sale_mode=effective_sale_mode,
                        months=payment_units_for_activation(payment, effective_sale_mode),
                        traffic_amount=effective_units,
                        provider_subscription=TRIBUTE_PROVIDER,
                        provider_notification=TRIBUTE_PROVIDER,
                        db_user=db_user,
                        log_prefix="Tribute Digital Product webhook",
                        activation_extra_kwargs={},
                        skip_referral_bonus=True,
                    )
                )
                if outcome is None:
                    return web.json_response(
                        {"ok": False, "error": "activation_failed"},
                        status=500,
                    )

            self._mark_product_fulfilled(purchase, int(payment.payment_id))
            await session.commit()
            return web.json_response(
                {
                    "ok": True,
                    "status": "processed",
                    "payment_id": int(payment.payment_id),
                }
            )

    async def _process_digital_product_refund(
        self,
        payload: TributeDigitalProductRefundPayload,
    ) -> web.Response:
        async with self.async_session_factory() as session:
            purchase = await tribute_dal.get_product_purchase_for_update(
                session,
                payload.purchase_id,
            )
            if purchase is None:
                binding = _product_binding_for_event(self.settings, payload.product_id)
                purchase, _created = await tribute_dal.ensure_product_purchase(
                    session,
                    {
                        "tribute_purchase_id": payload.purchase_id,
                        "tribute_transaction_id": payload.transaction_id,
                        "tribute_product_id": payload.product_id,
                        "trb_user_id": payload.trb_user_id,
                        "telegram_user_id": payload.telegram_user_id,
                        "tariff_key": binding.tariff_key if binding else None,
                        "sale_mode": binding.sale_mode if binding else None,
                        "units": binding.units if binding else None,
                        "amount": payload.amount,
                        "currency": payload.currency,
                        "status": "refunded",
                        "refunded_at": _as_utc(payload.refunded_at),
                        "refund_reason": payload.refund_reason,
                    },
                )

            mismatch = self._product_identity_mismatch(purchase, payload)
            if mismatch is not None:
                logger.error(
                    "Quarantining mismatched Tribute Digital Product refund %s: %s.",
                    payload.purchase_id,
                    mismatch,
                )
                await session.commit()
                return web.json_response({"ok": True, "status": "quarantined", "reason": mismatch})

            purchase.status = "refunded"
            purchase.status_reason = None
            purchase.refunded_at = _as_utc(payload.refunded_at)
            purchase.refund_reason = payload.refund_reason
            if purchase.payment_id is not None:
                await payment_dal.update_payment_status_by_db_id(
                    session,
                    int(purchase.payment_id),
                    "refunded",
                )
                logger.warning(
                    "Tribute Digital Product purchase %s was refunded after fulfillment; "
                    "traffic is not clawed back automatically.",
                    payload.purchase_id,
                )
            await session.commit()
            return web.json_response({"ok": True, "status": "refunded"})
