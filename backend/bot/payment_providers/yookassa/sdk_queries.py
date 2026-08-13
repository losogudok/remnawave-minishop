import logging
from typing import TYPE_CHECKING, Any

from yookassa import Payment as YooKassaPayment

logger = logging.getLogger(__name__)


class YooKassaSdkQueryMixin:
    if TYPE_CHECKING:

        @property
        def configured(self) -> bool: ...
        async def _run_sdk_call(self, *args: Any, **kwargs: Any) -> Any: ...

    async def get_payment_info(self, payment_id_in_yookassa: str) -> dict[str, Any] | None:
        if not self.configured:
            logger.error("YooKassa is not configured. Cannot get payment info.")
            return None
        try:
            logger.info("Fetching payment info from YooKassa for ID: %s", payment_id_in_yookassa)
            payment_info_yk = await self._run_sdk_call(
                "payment.find_one",
                YooKassaPayment.find_one,
                payment_id_in_yookassa,
            )
            if not payment_info_yk:
                logger.warning(
                    "No payment info found in YooKassa for ID: %s", payment_id_in_yookassa
                )
                return None

            logger.info(
                "YooKassa payment info for %s: Status=%s, Paid=%s",
                payment_id_in_yookassa,
                payment_info_yk.status,
                payment_info_yk.paid,
            )
            pm = getattr(payment_info_yk, "payment_method", None)
            pm_payload: dict[str, Any] = {}
            if pm:
                pm_id = getattr(pm, "id", None)
                pm_type = getattr(pm, "type", None)
                pm_title = getattr(pm, "title", None)
                account_number = getattr(pm, "account_number", None) or getattr(pm, "account", None)
                card_obj = getattr(pm, "card", None)
                last4_val = None
                if card_obj and hasattr(card_obj, "last4"):
                    last4_val = card_obj.last4
                elif isinstance(account_number, str) and len(account_number) >= 4:
                    last4_val = account_number[-4:]
                pm_payload = {
                    "id": pm_id,
                    "type": pm_type,
                    "saved": bool(getattr(pm, "saved", False)),
                    "title": pm_title,
                    "account_number": account_number,
                    "card": (
                        {
                            "first6": getattr(card_obj, "first6", None),
                            "last4": getattr(card_obj, "last4", None),
                            "expiry_month": getattr(card_obj, "expiry_month", None),
                            "expiry_year": getattr(card_obj, "expiry_year", None),
                            "card_type": getattr(card_obj, "card_type", None),
                        }
                        if card_obj is not None
                        else None
                    ),
                    "card_last4": last4_val,
                }
            confirmation = getattr(payment_info_yk, "confirmation", None)
            confirmation_url = (
                getattr(confirmation, "confirmation_url", None) if confirmation else None
            )
            cancellation = getattr(payment_info_yk, "cancellation_details", None)
            cancellation_details = (
                {
                    "party": getattr(cancellation, "party", None),
                    "reason": getattr(cancellation, "reason", None),
                }
                if cancellation is not None
                else None
            )
            return {
                "id": payment_info_yk.id,
                "status": payment_info_yk.status,
                "paid": payment_info_yk.paid,
                "amount_value": float(payment_info_yk.amount.value),
                "amount_currency": payment_info_yk.amount.currency,
                "metadata": payment_info_yk.metadata,
                "description": payment_info_yk.description,
                "refundable": payment_info_yk.refundable,
                "created_at": payment_info_yk.created_at.isoformat()
                if hasattr(payment_info_yk.created_at, "isoformat")
                else str(payment_info_yk.created_at),
                "captured_at": payment_info_yk.captured_at.isoformat()
                if getattr(payment_info_yk, "captured_at", None)
                and hasattr(payment_info_yk.captured_at, "isoformat")
                else None,
                "payment_method": pm_payload,
                "confirmation_url": confirmation_url,
                "test_mode": getattr(payment_info_yk, "test", None),
                "cancellation_details": cancellation_details,
            }
        except Exception:
            logger.exception("YooKassa get payment info for %s failed.", payment_id_in_yookassa)
            return None

    async def cancel_payment(self, payment_id_in_yookassa: str) -> bool:
        if not self.configured:
            logger.error("YooKassa is not configured. Cannot cancel payment.")
            return False
        try:
            await self._run_sdk_call(
                "payment.cancel",
                YooKassaPayment.cancel,
                payment_id_in_yookassa,
            )
            logger.info("Cancelled YooKassa payment %s", payment_id_in_yookassa)
            return True
        except Exception:
            logger.exception("Failed to cancel YooKassa payment %s.", payment_id_in_yookassa)
            return False
