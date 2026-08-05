import asyncio
from types import SimpleNamespace

from bot.payment_providers.freekassa.service import FreeKassaService


def _service(*, payment_ip: str | None, payment_method_id: int | None) -> FreeKassaService:
    service = object.__new__(FreeKassaService)
    service.config = SimpleNamespace(
        ENABLED=True,
        MERCHANT_ID="123456",
        API_KEY="api-key",
        PAYMENT_IP=payment_ip,
        PAYMENT_METHOD_ID=payment_method_id,
    )
    service.default_currency = "RUB"
    return service


def test_checkout_configuration_requires_payment_ip_and_method_id() -> None:
    assert not _service(payment_ip=None, payment_method_id=44).configured
    assert not _service(payment_ip="203.0.113.10", payment_method_id=None).configured
    assert _service(payment_ip="203.0.113.10", payment_method_id=44).configured


def test_api_operations_remain_configured_without_checkout_ip() -> None:
    service = _service(payment_ip=None, payment_method_id=44)

    assert service.api_configured


def test_create_order_reports_missing_checkout_ip() -> None:
    service = _service(payment_ip=None, payment_method_id=44)

    success, response = asyncio.run(
        service.create_order(
            payment_db_id=77,
            user_id=1001,
            months=1,
            amount=199.0,
            currency="RUB",
            payment_method_id=service.payment_method_id,
        )
    )

    assert not success
    assert response == {"message": "missing_ip"}
