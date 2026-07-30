"""Admin endpoint that exposes the Tribute Creator catalog to the tariff editor."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bot.app.web.admin_api_impl import tariffs_tribute
from bot.payment_providers.tribute.creator import (
    TributeCreatorApiError,
    TributeCreatorCatalog,
    TributeCreatorProduct,
    TributeCreatorSubscription,
)


class _FakeRequest:
    def __init__(self, service: object | None) -> None:
        self.app: dict[str, object] = {}
        if service is not None:
            self.app["tribute_service"] = service


def _catalog() -> TributeCreatorCatalog:
    return TributeCreatorCatalog(
        subscriptions=(
            TributeCreatorSubscription.model_validate(
                {
                    "subscriptionId": 101,
                    "name": "Standard",
                    "currency": "rub",
                    "periods": [
                        {"periodId": 1001, "period": "monthly", "price": 299},
                        {"periodId": 1077, "period": "weekly", "price": 99},
                    ],
                }
            ),
        ),
        products=(
            TributeCreatorProduct.model_validate(
                {
                    "id": 501,
                    "name": "50 GB",
                    "type": "digital",
                    "status": "approved",
                    "amount": 19900,
                    "currency": "rub",
                    "webLink": "https://web.tribute.tg/p/501",
                }
            ),
        ),
    )


def _body(response: Any) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(response.text)
    return payload


@pytest.fixture(autouse=True)
def _bypass_admin_auth(monkeypatch) -> None:
    monkeypatch.setattr(tariffs_tribute, "_require_admin_user_id", lambda request: 1)


def test_catalog_route_serializes_subscriptions_and_products() -> None:
    service = SimpleNamespace(fetch_creator_catalog=AsyncMock(return_value=_catalog()))

    response = asyncio.run(
        tariffs_tribute.admin_tariffs_tribute_catalog_route(_FakeRequest(service))
    )

    assert response.status == 200
    body = _body(response)
    assert body["subscriptions"] == [
        {
            "subscription_id": 101,
            "name": "Standard",
            "currency": "rub",
            "periods": [
                {"period_id": 1001, "period": "monthly", "price": 299.0, "months": 1},
                # Minishop cannot sell a weekly period, so it carries no duration.
                {"period_id": 1077, "period": "weekly", "price": 99.0, "months": None},
            ],
        }
    ]
    assert body["products"] == [
        {
            "product_id": 501,
            "name": "50 GB",
            "type": "digital",
            "status": "approved",
            "price": 199.0,
            "currency": "rub",
            "link": "https://web.tribute.tg/p/501",
        }
    ]


def test_catalog_route_reports_a_missing_provider() -> None:
    response = asyncio.run(tariffs_tribute.admin_tariffs_tribute_catalog_route(_FakeRequest(None)))

    assert response.status == 503
    assert _body(response)["error"] == "tribute_unavailable"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("not_configured", 503),
        ("unauthorized", 503),
        ("rate_limited", 502),
        ("request_failed", 502),
    ],
)
def test_catalog_route_maps_creator_api_errors(code: str, status: int) -> None:
    service = SimpleNamespace(
        fetch_creator_catalog=AsyncMock(side_effect=TributeCreatorApiError(code))
    )

    response = asyncio.run(
        tariffs_tribute.admin_tariffs_tribute_catalog_route(_FakeRequest(service))
    )

    assert response.status == status
    assert _body(response)["error"] == f"tribute_{code}"


def test_catalog_route_survives_an_unexpected_failure() -> None:
    service = SimpleNamespace(fetch_creator_catalog=AsyncMock(side_effect=RuntimeError("boom")))

    response = asyncio.run(
        tariffs_tribute.admin_tariffs_tribute_catalog_route(_FakeRequest(service))
    )

    assert response.status == 502
    assert _body(response)["error"] == "tribute_request_failed"


def test_product_price_converts_minor_units() -> None:
    product = _catalog().products[0]

    assert product.price == Decimal("199")
