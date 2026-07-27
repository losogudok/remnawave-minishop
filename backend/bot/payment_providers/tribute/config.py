from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import subscription_dal

from ..base import ProviderEnvConfig, provider_env_file
from ..shared import sale_mode_base, sale_mode_tariff_key
from .models import TributeSubscriptionPayload, TributeWebhookEnvelope
from .shop import (
    TRIBUTE_SHOP_ORDER_MAX_MINOR,
    TRIBUTE_SHOP_ORDER_MIN_MINOR,
    TRIBUTE_SHOP_WEBHOOK_EVENTS,
    TributeShopWebhookPayload,
    normalize_shop_currency,
    tribute_shop_period_for_months,
)

TRIBUTE_PROVIDER = "tribute"
TRIBUTE_SERVICE_KEY = "tribute_service"
TRIBUTE_PENDING_STATUS = "pending_tribute"
TRIBUTE_SIGNATURE_HEADER = "trbt-signature"
TRIBUTE_WEBHOOK_EVENTS = (
    frozenset(
        {
            "new_subscription",
            "renewed_subscription",
            "cancelled_subscription",
            "new_digital_product",
            "digital_product_refunded",
        }
    )
    | TRIBUTE_SHOP_WEBHOOK_EVENTS
)
TRIBUTE_SUBSCRIPTION_TYPES = frozenset({"regular", "gift", "trial"})
TRIBUTE_MAX_WEBHOOK_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class TributePlanBinding:
    tariff_key: str
    months: int
    link: str
    subscription_id: int
    period_id: int


@dataclass(frozen=True, slots=True)
class TributeProductBinding:
    tariff_key: str
    sale_mode: str
    units: float
    link: str
    product_id: int


class TributeConfig(ProviderEnvConfig):
    model_config = SettingsConfigDict(
        env_file=provider_env_file(),
        env_file_encoding="utf-8",
        env_prefix="TRIBUTE_",
        extra="ignore",
    )

    ENABLED: bool = Field(default=False)
    API_KEY: str | None = None
    SHOP_ENABLED: bool = Field(default=False)
    SHOP_ID: int | None = Field(default=None, gt=0, le=2**64 - 1)

    @field_validator("API_KEY", mode="before")
    @classmethod
    def _strip_api_key(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("SHOP_ID", mode="before")
    @classmethod
    def _parse_shop_id(cls, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError("TRIBUTE_SHOP_ID must be a positive integer")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if not normalized.isascii() or not normalized.isdigit():
                raise ValueError("TRIBUTE_SHOP_ID must be a positive integer")
            return int(normalized)
        raise ValueError("TRIBUTE_SHOP_ID must be a positive integer")

    @model_validator(mode="after")
    def _validate_shop_config(self) -> Self:
        if self.SHOP_ENABLED and self.SHOP_ID is None:
            raise ValueError("TRIBUTE_SHOP_ID is required when TRIBUTE_SHOP_ENABLED is true")
        return self


class TributePresentation(ProviderEnvConfig):
    model_config = SettingsConfigDict(
        env_file=provider_env_file(),
        env_file_encoding="utf-8",
        env_prefix="PAYMENT_TRIBUTE_",
        extra="ignore",
    )

    WEBAPP_LABEL_RU: str | None = None
    WEBAPP_LABEL_EN: str | None = None
    WEBAPP_ICON: str | None = None
    TELEGRAM_LABEL_RU: str | None = None
    TELEGRAM_LABEL_EN: str | None = None
    TELEGRAM_EMOJI: str | None = None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _event_order(name: str) -> int:
    return {
        "new_subscription": 1,
        "renewed_subscription": 2,
        "cancelled_subscription": 3,
    }.get(name, 0)


def _normalized_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds")


def _subscriber_key(payload: TributeSubscriptionPayload) -> str:
    return str(payload.trb_user_id or f"T-{int(payload.telegram_user_id or 0)}")


def _event_fingerprint(
    envelope: TributeWebhookEnvelope,
    payload: TributeSubscriptionPayload,
) -> str:
    # sent_at deliberately stays out: Tribute changes it for delivery retries.
    semantic_parts = (
        envelope.name,
        str(payload.subscription_id),
        str(payload.period_id),
        _subscriber_key(payload),
        str(payload.telegram_user_id or 0),
        _normalized_datetime(envelope.created_at),
        _normalized_datetime(payload.expires_at),
    )
    return hashlib.sha256("\x00".join(semantic_parts).encode("utf-8")).hexdigest()


def _shop_event_fingerprint(
    envelope: TributeWebhookEnvelope,
    payload: TributeShopWebhookPayload,
) -> str:
    # ``sent_at`` changes on retries. ``created_at`` is the stable occurrence
    # time and differentiates recurring charges that reuse the same order UUID.
    semantic_parts = (
        envelope.name,
        str(payload.uuid),
        _normalized_datetime(envelope.created_at),
        str(getattr(payload, "transaction_id", "") or ""),
        str(getattr(payload, "charge_retries", "") or ""),
        str(getattr(payload, "status", "") or ""),
        str(getattr(payload, "error_code", "") or ""),
    )
    return hashlib.sha256("\x00".join(semantic_parts).encode("utf-8")).hexdigest()


def _tariff_tribute_config(tariff: Any) -> Any | None:
    tribute = getattr(tariff, "tribute", None)
    if not tribute:
        return None
    if not getattr(tribute, "link", None) or not getattr(tribute, "subscription_id", None):
        return None
    return tribute


def _product_for_units(tribute: Any, kind: str, units: float) -> Any | None:
    resolver = getattr(tribute, "product_for_units", None)
    if callable(resolver):
        return resolver(kind, units)
    products = getattr(
        tribute,
        "premium_traffic_products" if kind == "premium_traffic" else "traffic_products",
        {},
    )
    for raw_units, product in (products or {}).items():
        try:
            if float(raw_units) == units:
                return product
        except (TypeError, ValueError):
            continue
    return None


def _product_binding_for_checkout(
    settings: Any,
    *,
    sale_mode: str,
    months: Any,
) -> TributeProductBinding | None:
    base = sale_mode_base(sale_mode)
    kind = "premium_traffic" if base == "premium_topup" else "traffic"
    if base not in {"traffic_package", "topup", "premium_topup"}:
        return None
    tariff_key = sale_mode_tariff_key(sale_mode)
    tariffs_config = settings.tariffs_config
    if not tariff_key or tariffs_config is None:
        return None
    try:
        units = float(months)
    except (TypeError, ValueError, OverflowError):
        return None
    if units <= 0:
        return None
    try:
        tariff = tariffs_config.require(tariff_key)
    except Exception:
        return None
    tribute = getattr(tariff, "tribute", None)
    if tribute is None:
        return None
    product = _product_for_units(tribute, kind, units)
    if product is None:
        return None
    product_id = int(getattr(product, "product_id", 0) or 0)
    link = str(getattr(product, "link", "") or "").strip()
    if product_id <= 0 or not link:
        return None
    return TributeProductBinding(
        tariff_key=str(tariff.key),
        sale_mode=f"{base}@{tariff.key}",
        units=units,
        link=link,
        product_id=product_id,
    )


def _binding_for_checkout(
    settings: Any,
    *,
    sale_mode: str,
    months: Any,
) -> TributePlanBinding | TributeProductBinding | None:
    if sale_mode_base(sale_mode) != "subscription":
        return _product_binding_for_checkout(
            settings,
            sale_mode=sale_mode,
            months=months,
        )
    if "hwid_renewal" in sale_mode:
        return None
    tariff_key = sale_mode_tariff_key(sale_mode)
    tariffs_config = settings.tariffs_config
    if not tariff_key or tariffs_config is None:
        return None
    try:
        normalized_months = int(float(months))
    except (TypeError, ValueError, OverflowError):
        return None
    if normalized_months <= 0 or float(months) != normalized_months:
        return None
    try:
        tariff = tariffs_config.require(tariff_key)
    except Exception:
        return None
    tribute = _tariff_tribute_config(tariff)
    if tribute is None:
        return None
    period_id = getattr(tribute, "period_ids", {}).get(str(normalized_months))
    if not period_id:
        return None
    return TributePlanBinding(
        tariff_key=str(tariff.key),
        months=normalized_months,
        link=str(tribute.link),
        subscription_id=int(tribute.subscription_id),
        period_id=int(period_id),
    )


def _binding_for_event(
    settings: Any,
    payload: TributeSubscriptionPayload,
) -> TributePlanBinding | None:
    tariffs_config = settings.tariffs_config
    if tariffs_config is None:
        return None
    matches: list[TributePlanBinding] = []
    for tariff in getattr(tariffs_config, "tariffs", ()) or ():
        tribute = _tariff_tribute_config(tariff)
        if tribute is None or int(tribute.subscription_id) != payload.subscription_id:
            continue
        period_ids = {
            str(months): int(period_id)
            for months, period_id in (getattr(tribute, "period_ids", {}) or {}).items()
        }
        matching_months = [
            int(months)
            for months, period_id in period_ids.items()
            if int(period_id) == payload.period_id
        ]
        if not matching_months and payload.type == "trial" and period_ids:
            # Tribute trials have their own short provider period. The exact
            # grant still comes from expires_at; the smallest paid period is
            # only the local tariff attribution for the later conversion.
            matching_months = [min(int(months) for months in period_ids)]
        matches.extend(
            [
                TributePlanBinding(
                    tariff_key=str(tariff.key),
                    months=months,
                    link=str(tribute.link),
                    subscription_id=int(tribute.subscription_id),
                    period_id=payload.period_id,
                )
                for months in matching_months
            ]
        )
    return matches[0] if len(matches) == 1 else None


def _shop_context_supported(months: Any, sale_mode: str) -> bool:
    base = sale_mode_base(sale_mode)
    if "hwid_renewal" in str(sale_mode):
        return False
    if base == "subscription":
        try:
            tribute_shop_period_for_months(int(float(months)))
        except (TypeError, ValueError, OverflowError):
            return False
        return float(months) == int(float(months))
    return base in {
        "traffic",
        "traffic_package",
        "topup",
        "premium_topup",
        "hwid_device",
        "hwid_devices",
        "tariff_upgrade",
    }


def _shop_enabled_for_source(source: Any) -> bool:
    explicit = getattr(source, "TRIBUTE_SHOP_ENABLED", None)
    if explicit is not None:
        return bool(explicit)
    provider_config_value = getattr(source, "SHOP_ENABLED", None)
    if provider_config_value is not None:
        return bool(provider_config_value)
    try:
        from ..registry import get_provider_bundle

        bundle = get_provider_bundle(TRIBUTE_SERVICE_KEY)
        config = bundle.config if bundle else None
        return bool(config and getattr(config, "SHOP_ENABLED", False))
    except Exception:
        return False


def tribute_shop_amount_supported(
    source: Any,
    currency: Any,
    amount: Any,
) -> bool:
    """Fail open because the generic resolver cannot identify Creator fallback context."""

    del source, currency, amount
    return True


def tribute_shop_amount_metadata(
    source: Any,
    currency: Any,
) -> dict[str, Any] | None:
    """Expose dynamic Shop limits without disabling Creator fixed-link checkouts."""

    if not _shop_enabled_for_source(source):
        return None
    try:
        normalized_currency = str(normalize_shop_currency(str(currency))).upper()
    except (TypeError, ValueError):
        return None
    return {
        "shop_min_amount": float(Decimal(TRIBUTE_SHOP_ORDER_MIN_MINOR) / 100),
        "shop_max_amount": float(Decimal(TRIBUTE_SHOP_ORDER_MAX_MINOR) / 100),
        "shop_limit_currency": normalized_currency,
    }


def tribute_checkout_promo_supported(
    source: Any,
    months: Any,
    sale_mode: str,
    promo: Any,
) -> bool:
    """Return whether a resolved local promo can be represented by Tribute."""

    if not _shop_enabled_for_source(source) or not _shop_context_supported(months, sale_mode):
        # Creator links have provider-managed prices and cannot carry a local
        # checkout quote or its entitlement effects.
        return False
    if sale_mode_base(sale_mode) != "subscription":
        return True

    effects = getattr(promo, "effects", None)
    try:
        bonus_days = int(getattr(effects, "bonus_days", 0) or 0)
        duration_multiplier = float(getattr(effects, "duration_multiplier", 1.0) or 1.0)
        traffic_multiplier = float(getattr(effects, "traffic_multiplier", 1.0) or 1.0)
        discount_amount = float(getattr(promo, "discount_amount", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        discount_amount > 0
        and bonus_days == 0
        and duration_multiplier == 1.0
        and traffic_multiplier == 1.0
    )


def tribute_supports_checkout(settings: Any, months: Any, sale_mode: str) -> bool:
    if _shop_enabled_for_source(settings) and _shop_context_supported(months, sale_mode):
        return True
    return _binding_for_checkout(settings, sale_mode=sale_mode, months=months) is not None


def tribute_price_managed_externally(
    settings: Any,
    months: Any,
    sale_mode: str,
) -> bool:
    if _shop_enabled_for_source(settings) and _shop_context_supported(months, sale_mode):
        return False
    return _binding_for_checkout(settings, sale_mode=sale_mode, months=months) is not None


async def _has_active_tribute_recurrence(
    session: AsyncSession,
    *,
    user_id: int,
    sale_mode: str,
) -> bool:
    if sale_mode_base(sale_mode) not in {"subscription", "tariff_upgrade"}:
        return False
    subscription = await subscription_dal.get_active_subscription_by_user_id(
        session,
        int(user_id),
    )
    return bool(
        subscription is not None
        and str(subscription.provider or "").strip().lower() == TRIBUTE_PROVIDER
        and bool(subscription.auto_renew_enabled)
    )


def _product_binding_for_event(
    settings: Any,
    product_id: int,
) -> TributeProductBinding | None:
    tariffs_config = settings.tariffs_config
    if tariffs_config is None:
        return None
    resolver = getattr(tariffs_config, "tribute_product_target", None)
    if not callable(resolver):
        return None
    target = resolver(int(product_id))
    if target is None:
        return None
    tariff, kind, units = target
    tribute = getattr(tariff, "tribute", None)
    product = _product_for_units(tribute, str(kind), float(units)) if tribute else None
    if product is None or int(getattr(product, "product_id", 0) or 0) != int(product_id):
        return None
    if str(kind) == "premium_traffic":
        base = "premium_topup"
    elif str(getattr(tariff, "billing_model", "")) == "traffic":
        base = "traffic_package"
    else:
        base = "topup"
    return TributeProductBinding(
        tariff_key=str(tariff.key),
        sale_mode=f"{base}@{tariff.key}",
        units=float(units),
        link=str(getattr(product, "link", "") or ""),
        product_id=int(product_id),
    )
