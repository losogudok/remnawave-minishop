"""Typed Tribute Shop API contracts and wire-format helpers.

The Shop API accepts amounts in the smallest currency unit and measures title
and description limits in UTF-16 code units.  Keeping those details here makes
checkout code deterministic and keeps webhook parsing strict while remaining
forward-compatible with additive Tribute fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self, cast
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

TRIBUTE_SHOP_API_BASE_URL = "https://tribute.tg/api/v1"
TRIBUTE_SHOP_TITLE_UTF16_LIMIT = 100
TRIBUTE_SHOP_DESCRIPTION_UTF16_LIMIT = 300
TRIBUTE_SHOP_CUSTOMER_ID_LIMIT = 256
TRIBUTE_SHOP_ORDER_MIN_MINOR = 100
TRIBUTE_SHOP_ORDER_MAX_MINOR = 300_000
TRIBUTE_SHOP_SUPPORTED_CURRENCIES = frozenset({"eur", "rub", "usd"})
TRIBUTE_SHOP_WEBHOOK_EVENTS = frozenset(
    {
        "shop_order",
        "shop_order_charge_success",
        "shop_order_charge_failed",
        "shop_order_cancelled",
        "shop_order_refunded",
        "shop_order_payment_failed",
    }
)

_INT64_MAX = 2**63 - 1
_CURRENCY_MINOR_DIGITS: Mapping[str, int] = MappingProxyType(
    dict.fromkeys(TRIBUTE_SHOP_SUPPORTED_CURRENCIES, 2)
)


def _normalize_lower_token(value: object) -> object:
    if isinstance(value, str):
        return value.strip().lower()
    return value


def _parse_finite_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("amount must be a JSON number")
    parsed = Decimal(str(value))
    if not parsed.is_finite():
        raise ValueError("amount must be finite")
    return parsed


def _parse_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        raise ValueError("UUID must be a string")
    try:
        return UUID(value.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError("invalid UUID") from exc


def _parse_aware_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith(("Z", "z")):
            normalized = f"{normalized[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise ValueError("invalid ISO 8601 datetime") from exc
    else:
        raise ValueError("datetime must be an ISO 8601 string")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime must include a timezone")
    return parsed


type TributeShopCurrency = Annotated[
    Literal["eur", "rub", "usd"],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopPeriod = Annotated[
    Literal["onetime", "weekly", "monthly", "quarterly", "halfyearly", "yearly"],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopRecurringPeriod = Annotated[
    Literal["weekly", "monthly", "quarterly", "halfyearly", "yearly"],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopMemberStatus = Annotated[
    Literal["active", "cancelled"],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopRefundStatus = Annotated[
    Literal["initiated", "completed"],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopOrderStatus = Annotated[
    Literal["pending", "prepaid", "paid", "failed"],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopCancelReason = Annotated[
    Literal[
        "cancelled_by_seller",
        "charge_failed",
        "payment_method_expired",
        "stars_subscription_expired",
        "seller_unavailable",
        "last_charge_refunded",
    ],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopTrialPeriod = Annotated[
    Literal[
        "one_hour",
        "twelve_hours",
        "twenty_four_hours",
        "three_days",
        "seven_days",
    ],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopWebhookName = Annotated[
    Literal[
        "shop_order",
        "shop_order_charge_success",
        "shop_order_charge_failed",
        "shop_order_cancelled",
        "shop_order_refunded",
        "shop_order_payment_failed",
    ],
    BeforeValidator(_normalize_lower_token),
]
type TributeShopUUID = Annotated[UUID, BeforeValidator(_parse_uuid)]
type TributeShopAwareDatetime = Annotated[datetime, BeforeValidator(_parse_aware_datetime)]
type TributeShopDecimal = Annotated[Decimal, BeforeValidator(_parse_finite_decimal)]

TRIBUTE_SHOP_MONTHS_TO_PERIOD: Mapping[int, TributeShopRecurringPeriod] = MappingProxyType(
    {
        1: "monthly",
        3: "quarterly",
        6: "halfyearly",
        12: "yearly",
    }
)
TRIBUTE_SHOP_PERIOD_TO_MONTHS: Mapping[TributeShopRecurringPeriod, int] = MappingProxyType(
    {period: months for months, period in TRIBUTE_SHOP_MONTHS_TO_PERIOD.items()}
)


def normalize_shop_currency(currency: str) -> TributeShopCurrency:
    """Normalize and validate a currency supported by the Tribute Shop API."""

    if not isinstance(currency, str):
        raise TypeError("currency must be a string")
    normalized = currency.strip().lower()
    if normalized not in TRIBUTE_SHOP_SUPPORTED_CURRENCIES:
        supported = ", ".join(sorted(TRIBUTE_SHOP_SUPPORTED_CURRENCIES))
        raise ValueError(f"unsupported Tribute Shop currency; expected one of: {supported}")
    return cast(TributeShopCurrency, normalized)


def tribute_shop_major_to_minor(amount: Decimal, currency: str) -> int:
    """Convert an exact major-unit Decimal to Tribute's integer minor units.

    Floats and implicit rounding are deliberately rejected.  Callers must make
    a business decision about rounding before constructing the ``Decimal``.
    """

    normalized_currency = normalize_shop_currency(currency)
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be Decimal")
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    if amount <= 0:
        raise ValueError("amount must be greater than zero")

    numerator, denominator = amount.as_integer_ratio()
    factor = 10 ** _CURRENCY_MINOR_DIGITS[normalized_currency]
    minor_units, remainder = divmod(numerator * factor, denominator)
    if remainder:
        digits = _CURRENCY_MINOR_DIGITS[normalized_currency]
        raise ValueError(f"amount has more than {digits} fractional currency digits")
    if minor_units > _INT64_MAX:
        raise OverflowError("amount does not fit Tribute's signed int64 field")
    if minor_units < TRIBUTE_SHOP_ORDER_MIN_MINOR:
        raise ValueError(
            "amount is below Tribute Shop Order minimum "
            f"of {TRIBUTE_SHOP_ORDER_MIN_MINOR} minor units"
        )
    if minor_units > TRIBUTE_SHOP_ORDER_MAX_MINOR:
        raise ValueError(
            "amount exceeds Tribute Shop Order maximum "
            f"of {TRIBUTE_SHOP_ORDER_MAX_MINOR} minor units"
        )
    return minor_units


def tribute_shop_period_for_months(months: int) -> TributeShopRecurringPeriod:
    """Return Tribute's recurring period for a tariff duration."""

    if type(months) is not int:
        raise TypeError("months must be an integer")
    try:
        return TRIBUTE_SHOP_MONTHS_TO_PERIOD[months]
    except KeyError as exc:
        supported = ", ".join(str(value) for value in TRIBUTE_SHOP_MONTHS_TO_PERIOD)
        raise ValueError(f"unsupported recurring duration; expected one of: {supported}") from exc


def tribute_shop_months_for_period(period: str) -> int:
    """Return the tariff duration represented by a Tribute recurring period."""

    if not isinstance(period, str):
        raise TypeError("period must be a string")
    normalized = period.strip().lower()
    try:
        return TRIBUTE_SHOP_PERIOD_TO_MONTHS[cast(TributeShopRecurringPeriod, normalized)]
    except KeyError as exc:
        supported = ", ".join(TRIBUTE_SHOP_PERIOD_TO_MONTHS)
        raise ValueError(f"unsupported recurring period; expected one of: {supported}") from exc


def utf16_code_units(value: str) -> int:
    """Count UTF-16 code units without accepting lone surrogate characters."""

    if not isinstance(value, str):
        raise TypeError("value must be a string")
    units = 0
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("value contains a lone UTF-16 surrogate")
        units += 2 if codepoint > 0xFFFF else 1
    return units


def truncate_utf16(value: str, max_units: int) -> str:
    """Truncate text to a UTF-16 code-unit limit without splitting a character."""

    if not isinstance(value, str):
        raise TypeError("value must be a string")
    if type(max_units) is not int:
        raise TypeError("max_units must be an integer")
    if max_units < 0:
        raise ValueError("max_units must not be negative")

    used_units = 0
    result: list[str] = []
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("value contains a lone UTF-16 surrogate")
        character_units = 2 if codepoint > 0xFFFF else 1
        if used_units + character_units > max_units:
            break
        result.append(character)
        used_units += character_units
    return "".join(result)


def _required_shop_text(value: str, *, field_name: str, max_units: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    if utf16_code_units(normalized) > max_units:
        raise ValueError(f"{field_name} exceeds the {max_units} UTF-16 code-unit limit")
    return normalized


def truncate_shop_title(value: str) -> str:
    """Trim and safely fit a dynamic order title into Tribute's limit."""

    if not isinstance(value, str):
        raise TypeError("title must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("title must not be empty")
    return truncate_utf16(normalized, TRIBUTE_SHOP_TITLE_UTF16_LIMIT).rstrip()


def truncate_shop_description(value: str) -> str:
    """Trim and safely fit a dynamic order description into Tribute's limit."""

    if not isinstance(value, str):
        raise TypeError("description must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("description must not be empty")
    return truncate_utf16(normalized, TRIBUTE_SHOP_DESCRIPTION_UTF16_LIMIT).rstrip()


def _optional_trimmed_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _required_trimmed_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("value must not be empty")
    return normalized


def _https_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("URL must be an absolute https:// URL")
    return normalized


def _tribute_payment_url(value: str | None) -> str | None:
    normalized = _https_url(value)
    if normalized is None:
        return None
    parsed = urlsplit(normalized)
    hostname = str(parsed.hostname or "").lower().rstrip(".")
    official_hosts = ("tribute.tg", "t.me", "telegram.me")
    if not any(hostname == host or hostname.endswith(f".{host}") for host in official_hosts):
        raise ValueError("payment URL must use an official Tribute or Telegram host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("payment URL must not contain user information")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("payment URL contains an invalid port") from exc
    if port not in {None, 443}:
        raise ValueError("payment URL must use the default HTTPS port")
    return normalized


class _TributeShopWireModel(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        strict=True,
        validate_default=True,
    )


class TributeShopOrderRequest(_TributeShopWireModel):
    """Request body for ``POST /shop/orders``."""

    shop_id: StrictInt | None = Field(default=None, alias="shopId", gt=0)
    amount: StrictInt | None = Field(default=None, gt=0)
    currency: TributeShopCurrency
    title: StrictStr
    description: StrictStr
    success_url: StrictStr | None = Field(default=None, alias="successUrl")
    fail_url: StrictStr | None = Field(default=None, alias="failUrl")
    email: StrictStr | None = Field(default=None, max_length=320)
    comment: StrictStr | None = Field(default=None)
    customer_id: StrictStr | None = Field(
        default=None,
        alias="customerId",
        max_length=TRIBUTE_SHOP_CUSTOMER_ID_LIMIT,
    )
    period: TributeShopPeriod = "onetime"
    stars_amount: StrictInt | None = Field(default=None, alias="starsAmount", gt=0)
    image_url: StrictStr | None = Field(default=None, alias="imageUrl")
    first_period_amount: StrictInt | None = Field(
        default=None,
        alias="firstPeriodAmount",
        gt=0,
    )
    trial_period: TributeShopTrialPeriod | None = Field(default=None, alias="trialPeriod")

    @field_validator("title")
    @classmethod
    def _validate_title(cls, value: str) -> str:
        return _required_shop_text(
            value,
            field_name="title",
            max_units=TRIBUTE_SHOP_TITLE_UTF16_LIMIT,
        )

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str) -> str:
        return _required_shop_text(
            value,
            field_name="description",
            max_units=TRIBUTE_SHOP_DESCRIPTION_UTF16_LIMIT,
        )

    @field_validator("success_url", "fail_url", "image_url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        return _https_url(value)

    @field_validator("email", "comment")
    @classmethod
    def _trim_optional_text(cls, value: str | None) -> str | None:
        return _optional_trimmed_text(value)

    @field_validator("customer_id")
    @classmethod
    def _validate_customer_id(cls, value: str | None) -> str | None:
        normalized = _optional_trimmed_text(value)
        if value is not None and normalized is None:
            raise ValueError("customerId must not be empty")
        return normalized

    @model_validator(mode="after")
    def _validate_payment_options(self) -> Self:
        if self.amount is None and self.stars_amount is None:
            raise ValueError("amount is required unless starsAmount is provided")
        if self.stars_amount is not None and self.period not in {"onetime", "monthly"}:
            raise ValueError("starsAmount is only supported for onetime or monthly orders")
        if self.first_period_amount is not None:
            if self.period == "onetime":
                raise ValueError("firstPeriodAmount is only supported for recurring orders")
            if self.stars_amount is not None:
                raise ValueError("firstPeriodAmount and starsAmount are mutually exclusive")
            if self.amount is not None and self.first_period_amount == self.amount:
                raise ValueError("firstPeriodAmount must differ from amount")
        if self.trial_period is not None and self.period == "onetime":
            raise ValueError("trialPeriod is only supported for recurring orders")
        return self

    def to_api_payload(self) -> dict[str, Any]:
        """Serialize using Tribute's camelCase field names."""

        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class TributeShopOrderResponse(_TributeShopWireModel):
    """Security-sensitive snapshot returned after creating a Shop API order."""

    uuid: TributeShopUUID
    shop_id: StrictInt = Field(alias="shopId", gt=0)
    amount: StrictInt = Field(gt=0)
    currency: TributeShopCurrency
    status: TributeShopOrderStatus
    period: TributeShopPeriod
    first_period_amount: StrictInt | None = Field(
        default=None,
        alias="firstPeriodAmount",
        gt=0,
    )
    payment_url: StrictStr | None = Field(alias="paymentUrl")
    webapp_payment_url: StrictStr | None = Field(
        default=None,
        alias="webappPaymentUrl",
    )

    @field_validator("payment_url", "webapp_payment_url")
    @classmethod
    def _validate_payment_url(cls, value: str | None) -> str | None:
        return _tribute_payment_url(value)

    @model_validator(mode="after")
    def _require_payment_link(self) -> Self:
        if self.payment_url is None and self.webapp_payment_url is None:
            raise ValueError("Shop order response must include a usable payment URL")
        return self


class TributeShopActionResponse(_TributeShopWireModel):
    """Strict response shared by Shop cancel/refund mutation endpoints."""

    success: StrictBool
    message: StrictStr = Field(min_length=1, max_length=512)

    @field_validator("message")
    @classmethod
    def _normalize_message(cls, value: str) -> str:
        return _required_trimmed_text(value)


class TributeShopRefundResponse(TributeShopActionResponse):
    status: Annotated[Literal["initiated"], BeforeValidator(_normalize_lower_token)]


class TributeShopErrorResponse(_TributeShopWireModel):
    error: StrictStr = Field(min_length=1, max_length=128)

    @field_validator("error")
    @classmethod
    def _normalize_error(cls, value: str) -> str:
        return _required_trimmed_text(value).lower()


class TributeShopTransaction(_TributeShopWireModel):
    """Sell transaction returned by ``GET /shop/orders/{uuid}/transactions``."""

    id: StrictInt = Field(gt=0)
    type: Annotated[Literal["shop_order_sell"], BeforeValidator(_normalize_lower_token)]
    amount: TributeShopDecimal = Field(ge=0)
    currency: TributeShopCurrency
    is_refunded: StrictBool = Field(alias="isRefunded")
    is_refundable: StrictBool = Field(alias="isRefundable")


class TributeShopTransactionsResponse(_TributeShopWireModel):
    transactions: list[TributeShopTransaction]
    next_from: StrictStr = Field(alias="nextFrom")


class _TributeShopBasePayload(_TributeShopWireModel):
    uuid: TributeShopUUID
    shop_id: StrictInt = Field(alias="shopId", gt=0)
    amount: StrictInt = Field(ge=0)
    currency: TributeShopCurrency


class _TributeShopBuyerPayload(_TributeShopBasePayload):
    email: StrictStr | None = Field(default=None, max_length=320)
    customer_id: StrictStr | None = Field(
        default=None,
        alias="customerId",
        max_length=TRIBUTE_SHOP_CUSTOMER_ID_LIMIT,
    )
    stars_amount: StrictInt | None = Field(default=None, alias="starsAmount", ge=0)
    only_stars: StrictBool | None = Field(default=None, alias="onlyStars")
    first_period_amount: StrictInt | None = Field(
        default=None,
        alias="firstPeriodAmount",
        ge=0,
    )

    @field_validator("email", "customer_id")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        return _optional_trimmed_text(value)


class _TributeShopRecurringPayload(_TributeShopBuyerPayload):
    period: TributeShopRecurringPeriod
    is_recurrent: StrictBool | None = Field(default=None, alias="isRecurrent")
    member_status: TributeShopMemberStatus | None = Field(default=None, alias="memberStatus")
    member_expires_at: TributeShopAwareDatetime | None = Field(
        default=None,
        alias="memberExpiresAt",
    )


class TributeShopOrderPayload(_TributeShopBuyerPayload):
    """Payload for the initial successful ``shop_order`` event."""

    fee: StrictInt = Field(ge=0)
    status: Annotated[Literal["paid"], BeforeValidator(_normalize_lower_token)]
    is_recurrent: StrictBool = Field(alias="isRecurrent")
    period: TributeShopPeriod | None = None
    payment_token: TributeShopUUID | None = Field(default=None, alias="paymentToken")
    card_last4: StrictStr | None = Field(
        default=None,
        alias="cardLast4",
        min_length=4,
        max_length=4,
        pattern=r"^\d{4}$",
    )
    card_brand: StrictStr | None = Field(default=None, alias="cardBrand", max_length=64)
    member_status: TributeShopMemberStatus | None = Field(default=None, alias="memberStatus")
    member_expires_at: TributeShopAwareDatetime | None = Field(
        default=None,
        alias="memberExpiresAt",
    )
    is_trial: StrictBool | None = Field(default=None, alias="isTrial")
    trial_period: TributeShopTrialPeriod | None = Field(default=None, alias="trialPeriod")
    trial_ends_at: TributeShopAwareDatetime | None = Field(default=None, alias="trialEndsAt")

    @field_validator("card_brand")
    @classmethod
    def _normalize_card_brand(cls, value: str | None) -> str | None:
        return _optional_trimmed_text(value)


class TributeShopOrderChargeSuccessPayload(_TributeShopRecurringPayload):
    """Payload for a successful recurring charge."""


class TributeShopOrderChargeFailedPayload(_TributeShopRecurringPayload):
    """Payload for a failed recurring charge attempt."""

    charge_retries: StrictInt = Field(alias="chargeRetries", ge=1, le=3)


class TributeShopOrderCancelledPayload(_TributeShopRecurringPayload):
    """Payload for a cancelled recurring order."""

    cancel_reason: TributeShopCancelReason = Field(alias="cancelReason")


class TributeShopOrderRefundedPayload(_TributeShopBasePayload):
    """Payload for a Shop API transaction refund."""

    transaction_id: StrictInt = Field(alias="transactionId", gt=0)
    status: TributeShopRefundStatus
    refunded_at: TributeShopAwareDatetime | None = Field(default=None, alias="refundedAt")
    customer_id: StrictStr | None = Field(
        default=None,
        alias="customerId",
        max_length=TRIBUTE_SHOP_CUSTOMER_ID_LIMIT,
    )
    is_recurrent: StrictBool | None = Field(default=None, alias="isRecurrent")
    period: TributeShopPeriod | None = None
    stars_amount: StrictInt | None = Field(default=None, alias="starsAmount", ge=0)
    only_stars: StrictBool | None = Field(default=None, alias="onlyStars")
    first_period_amount: StrictInt | None = Field(
        default=None,
        alias="firstPeriodAmount",
        ge=0,
    )
    member_status: TributeShopMemberStatus | None = Field(default=None, alias="memberStatus")
    member_expires_at: TributeShopAwareDatetime | None = Field(
        default=None,
        alias="memberExpiresAt",
    )

    @field_validator("customer_id")
    @classmethod
    def _normalize_customer_id(cls, value: str | None) -> str | None:
        return _optional_trimmed_text(value)

    @model_validator(mode="after")
    def _validate_refund_completion(self) -> Self:
        if self.status == "completed" and self.refunded_at is None:
            raise ValueError("refundedAt is required for a completed refund")
        return self


class TributeShopOrderPaymentFailedPayload(_TributeShopBuyerPayload):
    """Payload for a failed initial customer-initiated payment."""

    error_code: StrictStr = Field(alias="errorCode", min_length=1, max_length=256)
    error_message: StrictStr = Field(alias="errorMessage", min_length=1, max_length=2048)

    @field_validator("error_code", "error_message")
    @classmethod
    def _normalize_error_text(cls, value: str) -> str:
        return _required_trimmed_text(value)


type TributeShopWebhookPayload = (
    TributeShopOrderPayload
    | TributeShopOrderChargeSuccessPayload
    | TributeShopOrderChargeFailedPayload
    | TributeShopOrderCancelledPayload
    | TributeShopOrderRefundedPayload
    | TributeShopOrderPaymentFailedPayload
)

TRIBUTE_SHOP_WEBHOOK_PAYLOAD_MODELS: Mapping[
    str,
    type[TributeShopWebhookPayload],
] = MappingProxyType(
    {
        "shop_order": TributeShopOrderPayload,
        "shop_order_charge_success": TributeShopOrderChargeSuccessPayload,
        "shop_order_charge_failed": TributeShopOrderChargeFailedPayload,
        "shop_order_cancelled": TributeShopOrderCancelledPayload,
        "shop_order_refunded": TributeShopOrderRefundedPayload,
        "shop_order_payment_failed": TributeShopOrderPaymentFailedPayload,
    }
)


def parse_tribute_shop_webhook_payload(
    name: str,
    payload: Mapping[str, Any],
) -> TributeShopWebhookPayload:
    """Validate and normalize a supported Shop API webhook payload."""

    if not isinstance(name, str):
        raise TypeError("webhook name must be a string")
    normalized_name = name.strip().lower()
    try:
        model = TRIBUTE_SHOP_WEBHOOK_PAYLOAD_MODELS[normalized_name]
    except KeyError as exc:
        raise ValueError(f"unsupported Tribute Shop webhook event: {normalized_name}") from exc
    return model.model_validate(dict(payload))


class TributeShopWebhookEnvelope(_TributeShopWireModel):
    """Common signed Shop API webhook envelope."""

    name: TributeShopWebhookName
    created_at: TributeShopAwareDatetime
    sent_at: TributeShopAwareDatetime
    payload: dict[str, Any]

    def parsed_payload(self) -> TributeShopWebhookPayload:
        return parse_tribute_shop_webhook_payload(self.name, self.payload)
