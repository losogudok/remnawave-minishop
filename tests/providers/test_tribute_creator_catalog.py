"""Creator catalog lookup that backs the tariff editor's Tribute bindings."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bot.payment_providers.tribute.config import TributeConfig
from bot.payment_providers.tribute.creator import (
    TRIBUTE_CREATOR_PRODUCTS_PAGE_SIZE,
    TributeCreatorApiError,
    fetch_creator_products,
    fetch_creator_subscriptions,
)
from bot.payment_providers.tribute.service import TributeService

API_KEY = "tribute-api-key"


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200, *, text: str | None = None) -> None:
        self.status = status
        self._text = text if text is not None else json.dumps(payload)

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSession:
    """Minimal aiohttp stand-in that records every Creator API call."""

    def __init__(self, *responses: _FakeResponse) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self._responses.pop(0)


def _subscriptions_payload() -> dict[str, Any]:
    return {
        "result": [
            {
                "subscriptionId": 101,
                "name": " Standard ",
                "currency": "RUB",
                "periods": [
                    {"periodId": 1001, "period": "monthly", "price": 299},
                    {"periodId": 1003, "period": "quarterly", "price": 799.5},
                    {"periodId": 1077, "period": "weekly", "price": 99},
                ],
            }
        ]
    }


def _product_row(product_id: int) -> dict[str, Any]:
    return {
        "id": product_id,
        "name": "50 GB",
        "type": "digital",
        "status": "approved",
        "amount": 19900,
        "currency": "RUB",
        "link": "https://telegram.me/tribute/app?startapp=p501",
        "webLink": "https://web.tribute.tg/p/501",
    }


def _service(config: TributeConfig | None = None) -> TributeService:
    service = object.__new__(TributeService)
    service.bot = SimpleNamespace()
    service.settings = SimpleNamespace()
    service.config = config or TributeConfig(ENABLED=True, API_KEY=API_KEY)
    service.i18n = SimpleNamespace()
    service.subscription_service = SimpleNamespace()
    service.referral_service = SimpleNamespace()
    return service


def test_fetch_subscriptions_maps_periods_to_local_months() -> None:
    session = _FakeSession(_FakeResponse(_subscriptions_payload()))

    subscriptions = asyncio.run(fetch_creator_subscriptions(session, API_KEY))

    assert len(subscriptions) == 1
    subscription = subscriptions[0]
    assert subscription.subscription_id == 101
    assert subscription.name == "Standard"
    assert subscription.currency == "rub"
    assert [period.months for period in subscription.periods] == [1, 3, None]
    assert subscription.periods[1].price == Decimal("799.5")
    assert session.calls[0]["url"] == "https://tribute.tg/api/v1/subscriptions"
    assert session.calls[0]["headers"] == {"Api-Key": API_KEY}


def test_fetch_subscriptions_skips_an_entry_it_cannot_model() -> None:
    payload = {
        "result": [
            {"subscriptionId": 0, "name": "Broken", "periods": []},
            {"subscriptionId": 101, "name": "Standard", "currency": "rub", "periods": []},
        ]
    }
    session = _FakeSession(_FakeResponse(payload))

    subscriptions = asyncio.run(fetch_creator_subscriptions(session, API_KEY))

    assert [item.subscription_id for item in subscriptions] == [101]


def test_fetch_subscriptions_rejects_an_unusable_body() -> None:
    session = _FakeSession(_FakeResponse({"result": "nope"}))

    with pytest.raises(TributeCreatorApiError) as exc_info:
        asyncio.run(fetch_creator_subscriptions(session, API_KEY))

    assert exc_info.value.code == "invalid_response"


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "unauthorized"), (403, "unauthorized"), (429, "rate_limited"), (500, "request_failed")],
)
def test_fetch_subscriptions_maps_error_statuses(status: int, code: str) -> None:
    session = _FakeSession(_FakeResponse({}, status=status))

    with pytest.raises(TributeCreatorApiError) as exc_info:
        asyncio.run(fetch_creator_subscriptions(session, API_KEY))

    assert exc_info.value.code == code
    assert exc_info.value.status == status


def test_fetch_products_reports_major_price_and_official_links() -> None:
    session = _FakeSession(_FakeResponse({"rows": [_product_row(501)], "meta": {"total": 1}}))

    products = asyncio.run(fetch_creator_products(session, API_KEY))

    assert len(products) == 1
    product = products[0]
    # Tribute reports the amount in minor units.
    assert product.price == Decimal("199")
    assert product.checkout_link == "https://telegram.me/tribute/app?startapp=p501"
    assert session.calls[0]["params"] == {
        "page": "1",
        "size": str(TRIBUTE_CREATOR_PRODUCTS_PAGE_SIZE),
    }


def test_fetch_products_drops_a_link_from_an_unofficial_host() -> None:
    row = _product_row(501) | {"link": "https://evil.example/p/501", "webLink": None}
    session = _FakeSession(_FakeResponse({"rows": [row]}))

    products = asyncio.run(fetch_creator_products(session, API_KEY))

    assert products[0].link is None
    assert products[0].checkout_link is None


def test_fetch_products_follows_pagination_until_a_short_page() -> None:
    full_page = {"rows": [_product_row(600 + index) for index in range(100)]}
    session = _FakeSession(
        _FakeResponse(full_page),
        _FakeResponse({"rows": [_product_row(501)]}),
    )

    products = asyncio.run(fetch_creator_products(session, API_KEY))

    assert len(products) == 101
    assert [call["params"]["page"] for call in session.calls] == ["1", "2"]


def test_fetch_catalog_requires_a_configured_provider() -> None:
    service = _service(TributeConfig(ENABLED=False, API_KEY=API_KEY))

    with pytest.raises(TributeCreatorApiError) as exc_info:
        asyncio.run(service.fetch_creator_catalog())

    assert exc_info.value.code == "not_configured"


def test_fetch_catalog_reads_subscriptions_and_products(monkeypatch) -> None:
    service = _service()
    session = _FakeSession(
        _FakeResponse(_subscriptions_payload()),
        _FakeResponse({"rows": [_product_row(501)]}),
    )
    monkeypatch.setattr(service, "_get_session", AsyncMock(return_value=session))

    catalog = asyncio.run(service.fetch_creator_catalog())

    assert [item.subscription_id for item in catalog.subscriptions] == [101]
    assert [item.id for item in catalog.products] == [501]
