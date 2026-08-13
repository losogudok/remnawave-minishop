from datetime import UTC, datetime
from typing import Any

from bot.infra import events
from bot.infra.event_payloads import (
    PaymentSucceededPayload,
    ReferralBonusGrantedPayload,
    SubscriptionCreatedPayload,
    SubscriptionExtendedPayload,
)

from ..base import normalize_payment_currency_code
from ..shared import (
    is_traffic_sale_base,
    parse_positive_int_units,
    payment_amount_and_currency_match,
    send_success_message_to_user,
)

HWID_DEVICE_SALE_BASES = {"hwid_device", "hwid_devices", "hwid_devices_renewal"}
DEFERRED_EVENTS_KEY = "_deferred_events"
DEFERRED_SUCCESS_MESSAGE_KEY = "_deferred_success_message"


def is_hwid_device_sale_base(sale_mode_base: str) -> bool:
    return sale_mode_base in HWID_DEVICE_SALE_BASES


async def emit_yookassa_success_events(
    event_payload: dict,
    *,
    send_success_message: Any = send_success_message_to_user,
) -> None:
    deferred_events = []
    deferred_success_message = None
    if isinstance(event_payload, dict):
        deferred_events = list(event_payload.pop(DEFERRED_EVENTS_KEY, []) or [])
        deferred_success_message = event_payload.pop(DEFERRED_SUCCESS_MESSAGE_KEY, None)
    await events.emit_model(PaymentSucceededPayload.model_validate(event_payload))
    for item in deferred_events:
        if isinstance(item, dict) and item.get("event") and isinstance(item.get("payload"), dict):
            event_name = item["event"]
            payload = item["payload"]
            if event_name == events.SUBSCRIPTION_EXTENDED:
                await events.emit_model(SubscriptionExtendedPayload.model_validate(payload))
            elif event_name == events.SUBSCRIPTION_CREATED:
                await events.emit_model(SubscriptionCreatedPayload.model_validate(payload))
            elif event_name == events.REFERRAL_BONUS_GRANTED:
                await events.emit_model(ReferralBonusGrantedPayload.model_validate(payload))
            else:
                await events.emit(event_name, payload)
    if isinstance(deferred_success_message, dict):
        await send_success_message(**deferred_success_message)


def metadata_value_present(value: Any | None) -> bool:
    return value is not None and str(value).strip() != ""


def metadata_int(value: Any | None) -> int | None:
    if not metadata_value_present(value):
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def metadata_float(value: Any | None) -> float | None:
    if not metadata_value_present(value):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def metadata_datetime(value: Any | None) -> datetime | None:
    if not metadata_value_present(value):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def resolve_yookassa_activation_amounts(
    *,
    sale_mode_base: str,
    subscription_months_raw: Any | None,
    traffic_gb_raw: Any | None,
    hwid_devices_raw: Any | None,
) -> tuple[float, float, int, int, float | None]:
    subscription_months = float(subscription_months_raw or 0)
    traffic_amount_gb = (
        float(traffic_gb_raw or 0)
        if metadata_value_present(traffic_gb_raw)
        else subscription_months
    )
    hwid_devices_count = 0
    if metadata_value_present(hwid_devices_raw):
        parsed_hwid_devices = parse_positive_int_units(hwid_devices_raw)
        if parsed_hwid_devices is None:
            raise ValueError("Invalid HWID device count")
        hwid_devices_count = parsed_hwid_devices
    elif is_hwid_device_sale_base(sale_mode_base):
        parsed_hwid_devices = parse_positive_int_units(subscription_months_raw)
        if parsed_hwid_devices is None:
            raise ValueError("Invalid HWID device count")
        hwid_devices_count = parsed_hwid_devices

    if sale_mode_base == "subscription":
        months_for_activation = int(subscription_months)
    elif is_hwid_device_sale_base(sale_mode_base):
        months_for_activation = hwid_devices_count
    else:
        months_for_activation = int(traffic_amount_gb)

    traffic_gb_for_activation = traffic_amount_gb if is_traffic_sale_base(sale_mode_base) else None
    return (
        subscription_months,
        traffic_amount_gb,
        hwid_devices_count,
        months_for_activation,
        traffic_gb_for_activation,
    )


def payment_amount_and_currency_are_valid(
    payment: Any,
    *,
    actual_amount: Any,
    actual_currency: Any,
) -> tuple[bool, str | None]:
    """Compare the provider settlement values with the immutable local order."""
    expected_currency = normalize_payment_currency_code(getattr(payment, "currency", None))
    received_currency = normalize_payment_currency_code(actual_currency, default="")
    if not expected_currency or received_currency != expected_currency:
        return False, "currency"
    amounts_match = payment_amount_and_currency_match(
        expected_amount=getattr(payment, "amount", None),
        expected_currency=getattr(payment, "currency", None),
        received_amount=actual_amount,
        received_currency=actual_currency,
        allow_overpayment=True,
    )
    return (True, None) if amounts_match else (False, "amount")
