from __future__ import annotations

import logging

from aiohttp import web

from bot.payment_providers.base import PaymentProviderSpec, WebAppPaymentContext
from bot.payment_providers.shared import reusable_webapp_payment_response

logger = logging.getLogger(__name__)


async def reuse_checkout_if_available(
    payment_context: WebAppPaymentContext,
    provider_spec: PaymentProviderSpec,
    *,
    match_reservations: bool = False,
    requested_promo_code: str | None = None,
    preserve_promo_code_case: bool = False,
    requested_partner_balance: bool = False,
) -> web.Response | None:
    if provider_spec.reuse_webapp_payment is None:
        return None
    try:
        return await reusable_webapp_payment_response(
            payment_context,
            provider_spec,
            match_reservations=match_reservations,
            requested_promo_code=requested_promo_code,
            preserve_promo_code_case=preserve_promo_code_case,
            requested_partner_balance=requested_partner_balance,
        )
    except Exception:
        logger.exception(
            "Failed to verify reusable payment: user_id=%s provider=%s reservations=%s",
            payment_context.user_id,
            provider_spec.provider_key,
            match_reservations,
        )
        return None
