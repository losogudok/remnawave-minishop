from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload

from db.models import Payment, PromoCode

logger = logging.getLogger(__name__)


async def find_recent_pending_provider_payment_for_checkout(
    session: AsyncSession,
    *,
    user_id: int,
    provider: str,
    pending_status: str,
    currency: str | None,
    sale_mode: str | None,
    months: int | None,
    purchased_gb: float | None,
    purchased_hwid_devices: int | None,
    hwid_traffic_bonus_bytes: int | None = None,
    tariff_key: str | None = None,
    tariff_change_quote_snapshot: str | None = None,
    entitlement_context_snapshot: str | None = None,
    since_minutes: int | None = None,
    match_reservations: bool = False,
    requested_promo_code: str | None = None,
    requested_promo_code_id: int | None = None,
    preserve_promo_code_case: bool = False,
    requested_partner_balance: bool = False,
) -> Payment | None:
    """Find the same pending purchase even when its funding quote has changed."""

    conditions = [
        Payment.user_id == user_id,
        Payment.provider == provider,
        func.lower(Payment.status).in_(tuple({str(pending_status).lower(), "pending"})),
        or_(
            Payment.provider_payment_id.isnot(None),
            Payment.yookassa_payment_id.isnot(None),
        ),
    ]
    if since_minutes is not None:
        cutoff = datetime.now(UTC) - timedelta(minutes=max(1, since_minutes))
        conditions.append(Payment.created_at >= cutoff)
    if currency is not None:
        conditions.append(func.upper(Payment.currency) == str(currency).strip().upper())
    if sale_mode is not None:
        conditions.append(Payment.sale_mode == sale_mode)
    if tariff_change_quote_snapshot is not None:
        conditions.append(Payment.tariff_change_quote_snapshot == tariff_change_quote_snapshot)
    else:
        conditions.append(Payment.tariff_change_quote_snapshot.is_(None))
    if entitlement_context_snapshot is not None:
        conditions.append(Payment.entitlement_context_snapshot == entitlement_context_snapshot)
    else:
        conditions.append(Payment.entitlement_context_snapshot.is_(None))
    if tariff_key is not None:
        conditions.append(Payment.tariff_key == tariff_key)
    if months is not None:
        conditions.append(Payment.subscription_duration_months == months)
    else:
        conditions.append(Payment.subscription_duration_months.is_(None))
    if purchased_gb is not None:
        conditions.append(func.abs(Payment.purchased_gb - float(purchased_gb)) < 0.0001)
    else:
        conditions.append(Payment.purchased_gb.is_(None))
    if purchased_hwid_devices is not None:
        conditions.append(Payment.purchased_hwid_devices == purchased_hwid_devices)
    else:
        conditions.append(Payment.purchased_hwid_devices.is_(None))
    if hwid_traffic_bonus_bytes is not None:
        conditions.append(Payment.hwid_traffic_bonus_bytes == hwid_traffic_bonus_bytes)
    else:
        conditions.append(Payment.hwid_traffic_bonus_bytes.is_(None))
    if match_reservations:
        normalized_promo_code = str(requested_promo_code or "").strip()
        if normalized_promo_code:
            public_code = func.coalesce(
                func.nullif(func.trim(PromoCode.archived_code), ""),
                PromoCode.code,
            )
            code_condition = (
                or_(
                    public_code == normalized_promo_code,
                    public_code == normalized_promo_code.upper(),
                )
                if preserve_promo_code_case
                else func.upper(public_code) == normalized_promo_code.upper()
            )
            conditions.append(Payment.promo_code_used.has(code_condition))
        elif requested_promo_code_id is not None:
            conditions.append(Payment.promo_code_id == int(requested_promo_code_id))
        else:
            conditions.append(Payment.promo_code_id.is_(None))
        partner_balance_amount = func.coalesce(Payment.partner_balance_amount_minor, 0)
        conditions.append(
            partner_balance_amount > 0 if requested_partner_balance else partner_balance_amount == 0
        )

    stmt = (
        select(Payment)
        .where(and_(*conditions))
        .options(joinedload(Payment.user), joinedload(Payment.promo_code_used))
        .order_by(Payment.created_at.desc(), Payment.payment_id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def find_later_equivalent_succeeded_payment(
    session: AsyncSession,
    payment: Any,
) -> Payment | None:
    """Return a later success that bought the same entitlement as ``payment``."""

    user_id = getattr(payment, "user_id", None)
    payment_id = getattr(payment, "payment_id", None)
    created_at = getattr(payment, "created_at", None)
    sale_mode = str(getattr(payment, "sale_mode", "") or "").strip()
    if user_id is None or payment_id is None or created_at is None or not sale_mode:
        return None

    conditions = [
        Payment.user_id == int(user_id),
        Payment.payment_id != int(payment_id),
        func.lower(Payment.status) == "succeeded",
        Payment.sale_mode == sale_mode,
        or_(Payment.created_at >= created_at, Payment.updated_at >= created_at),
        Payment.is_auto_renew == bool(getattr(payment, "is_auto_renew", False)),
    ]
    for column, value in (
        (Payment.tariff_key, getattr(payment, "tariff_key", None)),
        (
            Payment.subscription_duration_months,
            getattr(payment, "subscription_duration_months", None),
        ),
        (Payment.purchased_hwid_devices, getattr(payment, "purchased_hwid_devices", None)),
        (Payment.hwid_traffic_bonus_bytes, getattr(payment, "hwid_traffic_bonus_bytes", None)),
        (
            Payment.tariff_change_quote_snapshot,
            getattr(payment, "tariff_change_quote_snapshot", None),
        ),
        (
            Payment.entitlement_context_snapshot,
            getattr(payment, "entitlement_context_snapshot", None),
        ),
    ):
        conditions.append(column.is_(None) if value is None else column == value)
    purchased_gb = getattr(payment, "purchased_gb", None)
    conditions.append(
        Payment.purchased_gb.is_(None)
        if purchased_gb is None
        else func.abs(Payment.purchased_gb - float(purchased_gb)) < 0.0001
    )

    stmt = (
        select(Payment)
        .where(and_(*conditions))
        .order_by(Payment.updated_at.desc().nullslast(), Payment.payment_id.desc())
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def release_partner_balance_safely(
    session: AsyncSession,
    *,
    payment_id: int,
    status: str,
) -> None:
    """Release checkout balance in a savepoint without blocking reconciliation."""

    try:
        savepoint = await session.begin_nested()
        try:
            from bot.services.partner_checkout_balance import PartnerCheckoutBalanceService

            await PartnerCheckoutBalanceService.release_if_terminal(
                session,
                payment_id=payment_id,
                status=status,
            )
        except Exception:
            await savepoint.rollback()
            raise
        else:
            await savepoint.commit()
    except Exception:
        logger.exception(
            "Partner checkout balance release failed for payment %s; the reconciler will retry it.",
            payment_id,
        )
