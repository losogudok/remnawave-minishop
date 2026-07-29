from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from sqlalchemy.dialects import postgresql

from bot.app.web.webapp.serializers import _serialize_pending_promo_payment
from db.dal import payment_dal


class PendingPromoPaymentTests(IsolatedAsyncioTestCase):
    async def test_lookup_requires_owned_promo_and_reusable_pending_url(self) -> None:
        payment = SimpleNamespace(payment_id=17)
        session = SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: payment))
        )

        result = await payment_dal.get_latest_resumable_promo_payment(
            session,
            user_id=42,
        )

        self.assertIs(result, payment)
        statement = session.execute.await_args.args[0]
        rendered = str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        self.assertIn("payments.user_id = 42", rendered)
        self.assertIn("payments.promo_code_id IS NOT NULL", rendered)
        self.assertIn("payments.provider_payment_url IS NOT NULL", rendered)
        self.assertIn("lower(trim(payments.status)) LIKE 'pending_%%'", rendered)
        self.assertIn("ORDER BY payments.created_at DESC, payments.payment_id DESC", rendered)

    def test_serializer_preserves_discounted_checkout_details(self) -> None:
        payment = SimpleNamespace(
            payment_id=17,
            provider_payment_url="https://pay.example/17",
            provider="yookassa",
            status="pending_yookassa",
            amount=720.0,
            checkout_base_amount=900.0,
            checkout_discount_amount=180.0,
            currency="RUB",
            promo_discount_percent=20,
            subscription_duration_months=3,
            purchased_gb=None,
            purchased_hwid_devices=None,
            sale_mode="subscription@pro",
            tariff_key="pro",
            promo_effect_summary="-20%",
            promo_code_used=SimpleNamespace(code="SAVE20", archived_code=None),
            created_at=datetime(2026, 7, 29, 12, 30, tzinfo=UTC),
        )

        self.assertEqual(
            _serialize_pending_promo_payment(payment),
            {
                "payment_id": 17,
                "payment_url": "https://pay.example/17",
                "provider": "yookassa",
                "status": "pending_yookassa",
                "amount": 720.0,
                "base_amount": 900.0,
                "currency": "RUB",
                "discount_amount": 180.0,
                "discount_percent": 20.0,
                "months": 3,
                "purchased_gb": None,
                "purchased_hwid_devices": None,
                "sale_mode": "subscription@pro",
                "tariff_key": "pro",
                "promo_code": "SAVE20",
                "promo_effect_summary": "-20%",
                "created_at": "2026-07-29T12:30:00+00:00",
            },
        )
