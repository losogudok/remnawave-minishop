"""Creator catalog lookup that fills the Tribute fallback bindings in.

``subscription_id``, ``period_id`` and ``product_id`` exist only inside the
Tribute Creator API, so the tariff editor reads them here on demand instead of
making an operator transcribe numbers the dashboard never shows.  The catalog
also carries Tribute's own price, which is what the editor compares the local
price against: Creator links never receive it, so a silent divergence is the
usual configuration mistake.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

from bot.app.web.context import get_payment_service
from bot.app.web.route_contracts import (
    RouteContract,
    ok_envelope_for,
    register_contract,
    schema_ref,
)

from .auth import (
    _require_admin_user_id,
)
from .common import (
    _error,
    _ok,
)
from .response_schemas import (
    AdminTributeProductOut,
    AdminTributeSubscriptionOut,
    AdminTributeSubscriptionPeriodOut,
)

if TYPE_CHECKING:
    from bot.payment_providers.tribute.creator import TributeCreatorCatalog

logger = logging.getLogger(__name__)

_UNAVAILABLE_ERRORS = {"not_configured", "unauthorized"}

register_contract(
    "admin_tariffs_tribute_catalog_route",
    RouteContract(
        response_schema=ok_envelope_for(
            extra={
                "subscriptions": {
                    "type": "array",
                    "items": schema_ref(AdminTributeSubscriptionOut),
                },
                "products": {
                    "type": "array",
                    "items": schema_ref(AdminTributeProductOut),
                },
            },
        ),
        models=(
            AdminTributeSubscriptionOut,
            AdminTributeSubscriptionPeriodOut,
            AdminTributeProductOut,
        ),
    ),
)


def _catalog_payload(catalog: TributeCreatorCatalog) -> dict[str, object]:
    subscriptions = [
        AdminTributeSubscriptionOut(
            subscription_id=subscription.subscription_id,
            name=subscription.name,
            currency=subscription.currency,
            periods=[
                AdminTributeSubscriptionPeriodOut(
                    period_id=period.period_id,
                    period=period.period,
                    price=float(period.price),
                    months=period.months,
                )
                for period in subscription.periods
            ],
        ).model_dump(mode="json")
        for subscription in catalog.subscriptions
    ]
    products = [
        AdminTributeProductOut(
            product_id=product.id,
            name=product.name,
            type=product.type,
            status=product.status,
            price=float(product.price),
            currency=product.currency,
            link=product.checkout_link,
        ).model_dump(mode="json")
        for product in catalog.products
    ]
    return {"subscriptions": subscriptions, "products": products}


async def admin_tariffs_tribute_catalog_route(request: web.Request) -> web.Response:
    _require_admin_user_id(request)
    from bot.payment_providers.tribute.config import TRIBUTE_SERVICE_KEY
    from bot.payment_providers.tribute.creator import TributeCreatorApiError

    service = get_payment_service(request, TRIBUTE_SERVICE_KEY)
    fetch_catalog = getattr(service, "fetch_creator_catalog", None)
    if fetch_catalog is None:
        return _error(503, "tribute_unavailable", "Tribute provider is not available")

    try:
        catalog = await fetch_catalog()
    except TributeCreatorApiError as exc:
        status = 503 if exc.code in _UNAVAILABLE_ERRORS else 502
        logger.warning("Tribute Creator catalog lookup failed: %s", exc.code)
        return _error(status, f"tribute_{exc.code}", exc.code)
    except Exception as exc:
        logger.exception("Tribute Creator catalog lookup failed")
        return _error(502, "tribute_request_failed", str(exc))

    return _ok(_catalog_payload(catalog))
