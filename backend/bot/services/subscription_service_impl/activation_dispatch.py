"""Dispatch non-period purchases before paid subscription activation."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from db.dal import subscription_dal, user_dal

from ._typing import SubscriptionServiceMixinContract
from .entitlement_helpers import active_subscription_tariff_key as active_tariff_key

logger = logging.getLogger(__name__)


async def activate_non_period_purchase(
    service: SubscriptionServiceMixinContract,
    session: AsyncSession,
    *,
    user_id: int,
    months: int,
    payment_amount: float,
    payment_db_id: int,
    provider: str,
    sale_mode_base: str,
    traffic_gb: float | None,
    tariff_key: str | None,
    promo_code_id_from_payment: int | None,
    use_traffic_sale_mode: bool,
) -> tuple[bool, dict[str, object] | None]:
    if sale_mode_base in {"traffic", "traffic_package"} or (use_traffic_sale_mode):
        target_gb = traffic_gb if traffic_gb is not None else float(months)
        result = await service._activate_traffic_package(
            session=session,
            user_id=user_id,
            traffic_gb=target_gb,
            payment_amount=payment_amount,
            payment_db_id=payment_db_id,
            provider=provider,
            tariff_key=tariff_key,
            sale_mode="traffic_package" if service._tariffs_config() else "traffic",
            promo_code_id_from_payment=promo_code_id_from_payment,
        )
        return True, result if isinstance(result, dict) else None
    if sale_mode_base == "topup":
        if not tariff_key:
            tariff_key = await active_tariff_key(session, user_id)
        if not tariff_key:
            logger.error("Top-up activation requires tariff_key for user %s", user_id)
            return True, None
        result = await service.activate_topup(
            session=session,
            user_id=user_id,
            tariff_key=tariff_key,
            traffic_gb=traffic_gb if traffic_gb is not None else float(months),
            payment_amount=payment_amount,
            payment_db_id=payment_db_id,
            provider=provider,
            promo_code_id_from_payment=promo_code_id_from_payment,
        )
        return True, result if isinstance(result, dict) else None
    if sale_mode_base == "premium_topup":
        if not tariff_key:
            tariff_key = await active_tariff_key(session, user_id)
        if not tariff_key:
            logger.error("Premium top-up activation requires tariff_key for user %s", user_id)
            return True, None
        result = await service.activate_premium_topup(
            session=session,
            user_id=user_id,
            tariff_key=tariff_key,
            traffic_gb=traffic_gb if traffic_gb is not None else float(months),
            payment_amount=payment_amount,
            payment_db_id=payment_db_id,
            provider=provider,
            promo_code_id_from_payment=promo_code_id_from_payment,
        )
        return True, result if isinstance(result, dict) else None
    if sale_mode_base in {"hwid_device", "hwid_devices", "hwid_devices_renewal"}:
        target_devices = int(traffic_gb if traffic_gb is not None else months)
        result = await service.activate_hwid_device_topup(
            session=session,
            user_id=user_id,
            device_count=target_devices,
            payment_amount=payment_amount,
            payment_db_id=payment_db_id,
            provider=provider,
            tariff_key=tariff_key,
            renewal=sale_mode_base == "hwid_devices_renewal",
        )
        return True, result if isinstance(result, dict) else None
    if sale_mode_base == "tariff_upgrade":
        if not tariff_key:
            logger.error("Tariff upgrade activation requires tariff_key for user %s", user_id)
            return True, None
        await service._record_payment_context(
            session,
            payment_db_id,
            sale_mode="tariff_upgrade",
            tariff_key=tariff_key,
            purchased_gb=None,
        )
        result = await service.switch_tariff_without_payment(
            session,
            user_id,
            tariff_key,
            "paid_diff",
            payment_id=payment_db_id,
        )
        if result:
            sub = await subscription_dal.get_active_subscription_by_user_id(session, user_id)
            if sub:
                result["end_date"] = sub.end_date
                result["is_active"] = sub.is_active
                db_user = await user_dal.get_user_by_id(session, user_id)
                if db_user:
                    await service._send_payment_success_email(
                        db_user=db_user,
                        sale_mode="tariff_upgrade",
                        months=0,
                        traffic_gb=None,
                        payment_amount=payment_amount,
                        end_date=sub.end_date,
                        provider=provider,
                    )
        return True, result if isinstance(result, dict) else None

    return False, None
