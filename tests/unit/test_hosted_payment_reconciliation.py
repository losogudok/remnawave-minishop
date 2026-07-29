from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.payment_providers.shared.checkout_expiration import resolve_checkout_expiration
from bot.payment_providers.shared.reconciliation import (
    _inspect_provider_payment,
    refresh_hosted_payment_status,
)
from db.dal import payment_dal, payment_reconciliation_dal


def _payment(provider: str, *, provider_payment_id: str = "provider-1") -> SimpleNamespace:
    return SimpleNamespace(
        payment_id=17,
        user_id=42,
        provider=provider,
        provider_payment_id=provider_payment_id,
        yookassa_payment_id=None,
        status=f"pending_{provider}",
        checkout_expires_at=None,
    )


def test_resolve_checkout_expiration_supports_provider_timestamp_shapes() -> None:
    expected = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)

    payload = {"result": {"expirationDateTime": expected.isoformat()}}
    assert resolve_checkout_expiration(payload) == expected
    assert resolve_checkout_expiration({"expires_at": int(expected.timestamp())}) == expected
    assert resolve_checkout_expiration({"expiredAt": int(expected.timestamp() * 1000)}) == expected


def test_resolve_checkout_expiration_uses_only_an_exact_creation_ttl() -> None:
    started_at = datetime(2030, 1, 1, tzinfo=UTC)

    assert resolve_checkout_expiration({}, now=started_at) is None
    assert resolve_checkout_expiration(
        {},
        fallback_ttl_seconds=600,
        now=started_at,
    ) == started_at + timedelta(minutes=10)


@pytest.mark.parametrize(
    ("provider", "service", "expected_status"),
    [
        (
            "heleket",
            SimpleNamespace(
                get_payment_info=AsyncMock(
                    return_value=(
                        True,
                        {
                            "uuid": "provider-1",
                            "order_id": "17",
                            "status": "cancel",
                            "is_final": True,
                        },
                    )
                )
            ),
            "cancel",
        ),
        (
            "platega_sbp",
            SimpleNamespace(
                get_transaction=AsyncMock(
                    return_value=(
                        True,
                        {
                            "id": "provider-1",
                            "payload": json.dumps({"payment_db_id": "17"}),
                            "status": "CANCELED",
                        },
                    )
                )
            ),
            "canceled",
        ),
        (
            "lava",
            SimpleNamespace(
                get_invoice_status=AsyncMock(
                    return_value=(
                        True,
                        {
                            "id": "provider-1",
                            "order_id": "17",
                            "status": "expired",
                        },
                    )
                )
            ),
            "expired",
        ),
        (
            "paykilla",
            SimpleNamespace(
                get_invoice_details=AsyncMock(
                    return_value=(
                        True,
                        {
                            "id": "provider-1",
                            "clientOrderId": "17",
                            "status": "INVOICE_EXPIRED",
                        },
                    )
                )
            ),
            "invoice_expired",
        ),
        (
            "pally",
            SimpleNamespace(
                get_bill_status=AsyncMock(
                    return_value=(
                        True,
                        {
                            "bill_id": "provider-1",
                            "order_id": "17",
                            "status": "FAIL",
                        },
                    )
                )
            ),
            "fail",
        ),
        (
            "severpay",
            SimpleNamespace(
                get_payment=AsyncMock(
                    return_value=(
                        True,
                        {
                            "uid": "provider-1",
                            "order_id": "17",
                            "status": "decline",
                        },
                    )
                )
            ),
            "decline",
        ),
        (
            "stripe",
            SimpleNamespace(
                retrieve_checkout_session=AsyncMock(
                    return_value=(
                        True,
                        {
                            "id": "provider-1",
                            "metadata": {"payment_db_id": "17"},
                            "status": "expired",
                            "expires_at": 1_893_542_400,
                        },
                    )
                )
            ),
            "expired",
        ),
        (
            "cryptopay",
            SimpleNamespace(
                get_invoice=AsyncMock(
                    return_value=SimpleNamespace(
                        invoice_id="provider-1",
                        payload=json.dumps({"payment_db_id": "17"}),
                        status="expired",
                        expiration_date="2030-01-01T00:00:00Z",
                    )
                )
            ),
            "expired",
        ),
    ],
)
def test_provider_terminal_statuses_are_classified_as_failed(
    provider: str,
    service: SimpleNamespace,
    expected_status: str,
) -> None:
    lifecycle = asyncio.run(_inspect_provider_payment(service, _payment(provider)))

    assert lifecycle.state == "failed"
    assert lifecycle.provider_status == expected_status


def test_freekassa_pending_order_stays_reusable() -> None:
    service = SimpleNamespace(
        get_orders=AsyncMock(
            side_effect=[
                (True, {"orders": []}),
                (
                    True,
                    {
                        "orders": [
                            {
                                "merchant_order_id": "17",
                                "status": 0,
                            }
                        ]
                    },
                ),
            ]
        )
    )

    lifecycle = asyncio.run(_inspect_provider_payment(service, _payment("freekassa")))

    assert lifecycle.state == "pending"


def test_confirmed_terminal_failure_releases_checkout_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment = _payment("lava")
    failed_values = vars(payment).copy()
    failed_values["status"] = "failed"
    failed_payment = SimpleNamespace(**failed_values)
    service = SimpleNamespace(
        bot=object(),
        settings=object(),
        i18n=object(),
        get_invoice_status=AsyncMock(
            return_value=(
                True,
                {
                    "id": "provider-1",
                    "order_id": "17",
                    "status": "expired",
                },
            )
        ),
    )
    transition = AsyncMock(return_value=(failed_payment, True))
    reload_payment = AsyncMock(return_value=failed_payment)
    notify = AsyncMock()
    monkeypatch.setattr(payment_dal, "transition_provider_payment_to_terminal", transition)
    monkeypatch.setattr(payment_dal, "get_payment_by_db_id", reload_payment)
    monkeypatch.setattr(
        "bot.payment_providers.shared.reconciliation.notify_user_payment_failed",
        notify,
    )
    session = SimpleNamespace(commit=AsyncMock())

    result = asyncio.run(refresh_hosted_payment_status(session, payment, service))

    assert result.status == "failed"
    transition.assert_awaited_once_with(session, 17, "provider-1", "failed")
    notify.assert_awaited_once()


def test_expired_local_timestamp_does_not_override_provider_pending_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment = _payment("lava")
    payment.checkout_expires_at = datetime.now(UTC) - timedelta(hours=1)
    service = SimpleNamespace(
        get_invoice_status=AsyncMock(
            return_value=(
                True,
                {
                    "id": "provider-1",
                    "order_id": "17",
                    "status": "pending",
                },
            )
        )
    )
    mark_checked = AsyncMock(return_value=payment)
    transition = AsyncMock()
    monkeypatch.setattr(
        payment_reconciliation_dal,
        "mark_provider_payment_checked",
        mark_checked,
    )
    monkeypatch.setattr(payment_dal, "transition_provider_payment_to_terminal", transition)
    session = SimpleNamespace(commit=AsyncMock())

    result = asyncio.run(refresh_hosted_payment_status(session, payment, service))

    assert result.status == "pending_lava"
    transition.assert_not_awaited()
    mark_checked.assert_awaited_once()
