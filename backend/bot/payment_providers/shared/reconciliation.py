from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import payment_dal, payment_reconciliation_dal
from db.models import Payment

from .checkout_expiration import resolve_checkout_expiration
from .common import detached_payment_snapshot
from .webhooks import notify_user_payment_failed

logger = logging.getLogger(__name__)

LifecycleState = Literal["pending", "failed", "succeeded", "unknown"]

RECONCILABLE_PROVIDER_KEYS = (
    "cloudpayments",
    "cryptopay",
    "freekassa",
    "heleket",
    "lava",
    "overpay",
    "pally",
    "paykilla",
    "platega_crypto",
    "platega_sbp",
    "severpay",
    "stripe",
    "tribute",
)

_EXPIRY_ONLY_PROVIDER_KEYS = {"cloudpayments", "overpay", "tribute"}

_FAILED_STATUSES = {
    "cryptopay": {"expired"},
    "heleket": {"cancel", "fail", "system_fail", "wrong_amount"},
    "lava": {"cancel", "cancelled", "error", "expired", "failed"},
    "pally": {"cancelled", "canceled", "fail", "failed"},
    "paykilla": {
        "cancelled",
        "canceled",
        "expired",
        "failed",
        "invoice_cancelled",
        "invoice_expired",
        "payment_cancelled",
        "payment_failed",
    },
    "platega": {"canceled", "cancelled", "chargebacked"},
    "severpay": {"decline", "fail"},
}
_SUCCESS_STATUSES = {
    "cryptopay": {"paid"},
    "heleket": {"paid", "paid_over"},
    "lava": {"success"},
    "pally": {"overpaid", "success"},
    "paykilla": {"completed", "paid", "success"},
    "platega": {"confirmed"},
    "severpay": {"success"},
}
_PENDING_STATUSES = {
    "cryptopay": {"active"},
    "heleket": {"check"},
    "lava": {"created", "pending", "processing"},
    "pally": {"new", "process", "underpaid"},
    "paykilla": {"created", "new", "pending", "processing"},
    "platega": {"pending"},
    "severpay": {"new", "process"},
}


@dataclass(frozen=True, slots=True)
class ProviderLifecycle:
    state: LifecycleState
    provider_status: str = ""
    checkout_expires_at: datetime | None = None


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _state_for(provider: str, status: Any) -> LifecycleState:
    normalized = _normalized(status)
    if normalized in _FAILED_STATUSES.get(provider, set()):
        return "failed"
    if normalized in _SUCCESS_STATUSES.get(provider, set()):
        return "succeeded"
    if normalized in _PENDING_STATUSES.get(provider, set()):
        return "pending"
    return "unknown"


def _payload_payment_id(value: Any) -> str | None:
    try:
        payload = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return str(payload.get("payment_db_id") or payload.get("payment_id") or "").strip() or None


def _id_matches(data: dict[str, Any], expected: str, *keys: str) -> bool:
    returned = {str(data.get(key) or "").strip() for key in keys}
    returned.discard("")
    return not returned or expected in returned


async def _inspect_provider_payment(service: Any, payment: Payment) -> ProviderLifecycle:
    provider = _normalized(payment.provider)
    provider_id = str(payment.provider_payment_id or "").strip()
    if not provider_id:
        return ProviderLifecycle("unknown")

    success = False
    data: dict[str, Any] = {}
    status: Any = None
    state_provider = provider

    if provider == "heleket":
        success, data = await service.get_payment_info(provider_id)
        if success and (
            not _id_matches(data, provider_id, "uuid")
            or str(data.get("order_id") or "") != str(payment.payment_id)
        ):
            return ProviderLifecycle("unknown")
        status = data.get("payment_status") or data.get("status")
    elif provider in {"platega_sbp", "platega_crypto"}:
        success, data = await service.get_transaction(provider_id)
        if success and not _id_matches(data, provider_id, "id"):
            return ProviderLifecycle("unknown")
        payload_payment_id = _payload_payment_id(data.get("payload")) if success else None
        if payload_payment_id and payload_payment_id != str(payment.payment_id):
            return ProviderLifecycle("unknown")
        status = data.get("status")
        state_provider = "platega"
    elif provider == "lava":
        success, data = await service.get_invoice_status(
            order_id=str(payment.payment_id),
            invoice_id=provider_id,
        )
        if success and (
            not _id_matches(data, provider_id, "id", "invoice_id")
            or (
                data.get("order_id") is not None
                and str(data.get("order_id")) != str(payment.payment_id)
            )
            or (
                data.get("orderId") is not None
                and str(data.get("orderId")) != str(payment.payment_id)
            )
        ):
            return ProviderLifecycle("unknown")
        status = data.get("status")
    elif provider == "paykilla":
        success, data = await service.get_invoice_details(provider_id)
        if success and (
            not _id_matches(data, provider_id, "id")
            or str(data.get("clientOrderId") or "") != str(payment.payment_id)
        ):
            return ProviderLifecycle("unknown")
        status = data.get("status")
    elif provider == "pally":
        success, data = await service.get_bill_status(provider_id)
        if success and not _id_matches(data, provider_id, "id", "bill_id"):
            return ProviderLifecycle("unknown")
        order_id = data.get("order_id") if success else None
        if order_id is None and success:
            order_id = data.get("orderId")
        if order_id is not None and str(order_id) != str(payment.payment_id):
            return ProviderLifecycle("unknown")
        status = data.get("status") or data.get("Status")
    elif provider == "severpay":
        success, data = await service.get_payment(provider_id)
        if success and (
            not _id_matches(data, provider_id, "id", "uid")
            or str(data.get("order_id") or "") != str(payment.payment_id)
        ):
            return ProviderLifecycle("unknown")
        status = data.get("status")
    elif provider == "stripe":
        success, data = await service.retrieve_checkout_session(provider_id)
        if success and not _id_matches(data, provider_id, "id"):
            return ProviderLifecycle("unknown")
        metadata = data.get("metadata") if success else None
        if isinstance(metadata, dict):
            returned_payment_id = str(metadata.get("payment_db_id") or "").strip()
            if returned_payment_id and returned_payment_id != str(payment.payment_id):
                return ProviderLifecycle("unknown")
        checkout_status = _normalized(data.get("status"))
        payment_status = _normalized(data.get("payment_status"))
        if checkout_status == "expired":
            state: LifecycleState = "failed"
        elif checkout_status == "complete" and payment_status in {"paid", "no_payment_required"}:
            state = "succeeded"
        elif checkout_status == "open":
            state = "pending"
        else:
            state = "unknown"
        return ProviderLifecycle(
            state if success else "unknown",
            checkout_status or payment_status,
            resolve_checkout_expiration(data),
        )
    elif provider == "cryptopay":
        invoice = await service.get_invoice(provider_id)
        if invoice is None or str(invoice.invoice_id) != provider_id:
            return ProviderLifecycle("unknown")
        payload_payment_id = _payload_payment_id(invoice.payload)
        if payload_payment_id and payload_payment_id != str(payment.payment_id):
            return ProviderLifecycle("unknown")
        status = invoice.status
        success = True
        data = {"expiration_date": invoice.expiration_date}
    elif provider == "freekassa":
        paid_success, paid_data = await service.get_orders(
            payment_id=payment.payment_id,
            order_status=1,
        )
        if paid_success:
            for order in paid_data.get("orders") or []:
                if (
                    isinstance(order, dict)
                    and str(order.get("merchant_order_id") or "") == str(payment.payment_id)
                    and str(order.get("status")) == "1"
                ):
                    return ProviderLifecycle("succeeded", "1")
        pending_success, pending_data = await service.get_orders(
            payment_id=payment.payment_id,
            order_status=0,
        )
        if pending_success:
            for order in pending_data.get("orders") or []:
                if (
                    isinstance(order, dict)
                    and str(order.get("merchant_order_id") or "") == str(payment.payment_id)
                    and str(order.get("status")) == "0"
                ):
                    return ProviderLifecycle("pending", "0")
        return ProviderLifecycle("unknown")
    else:
        return ProviderLifecycle("unknown")

    if not success or not isinstance(data, dict):
        return ProviderLifecycle("unknown")
    provider_status = _normalized(status)
    state = _state_for(state_provider, provider_status)
    expires_at = resolve_checkout_expiration(data)
    if (
        provider == "heleket"
        and state == "pending"
        and expires_at is not None
        and expires_at <= datetime.now(UTC)
    ):
        state = "failed"
    return ProviderLifecycle(state, provider_status, expires_at)


async def refresh_hosted_payment_status(
    session: AsyncSession,
    payment: Payment,
    service: Any,
    *,
    expiration_grace_seconds: int = 30,
) -> Any:
    """Reconcile a pending hosted checkout against its provider's read API."""

    payment_snapshot = detached_payment_snapshot(payment)
    payment_id = int(payment_snapshot.payment_id)
    provider = _normalized(payment_snapshot.provider)

    if provider in {"wata", "wata_crypto"} and hasattr(service, "refresh_payment_status"):
        return await service.refresh_payment_status(session, payment_snapshot)

    lifecycle = await _inspect_provider_payment(service, payment_snapshot)
    expires_at = lifecycle.checkout_expires_at or getattr(
        payment_snapshot,
        "checkout_expires_at",
        None,
    )
    if (
        lifecycle.state == "pending"
        and expires_at is not None
        and expires_at + timedelta(seconds=max(0, expiration_grace_seconds)) <= datetime.now(UTC)
    ):
        # A provider can briefly return a stale pending state at its boundary.
        # Do not release the promo until the provider itself reports terminal.
        logger.info(
            "Provider %s still reports payment %s pending after checkout expiry",
            provider,
            payment_id,
        )

    locally_expired = bool(
        provider in _EXPIRY_ONLY_PROVIDER_KEYS
        and lifecycle.state == "unknown"
        and expires_at is not None
        and expires_at + timedelta(seconds=max(300, expiration_grace_seconds)) <= datetime.now(UTC)
    )
    if lifecycle.state == "failed" or locally_expired:
        provider_id = str(payment_snapshot.provider_payment_id or payment_id)
        updated, transitioned = await payment_dal.transition_provider_payment_to_terminal(
            session,
            payment_id,
            provider_id,
            "failed",
        )
        await session.commit()
        if updated is None:
            return payment_snapshot
        if transitioned:
            await notify_user_payment_failed(
                bot=service.bot,
                settings=service.settings,
                i18n=service.i18n,
                session=session,
                payment=updated,
            )
        return await payment_dal.get_payment_by_db_id(session, payment_id, fresh=True) or updated

    checked = await payment_reconciliation_dal.mark_provider_payment_checked(
        session,
        payment_id,
        checkout_expires_at=lifecycle.checkout_expires_at,
    )
    await session.commit()
    return checked or payment_snapshot
