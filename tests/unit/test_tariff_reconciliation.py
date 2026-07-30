import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config.tariffs_config import TariffsConfig
from db.tariff_reconciliation import reconcile_subscription_tariffs


def _tariff(key: str, *, legacy_keys: list[str] | None = None) -> dict:
    return {
        "key": key,
        "legacy_keys": legacy_keys or [],
        "names": {"en": key.title()},
        "billing_model": "period",
        "enabled": True,
        "monthly_gb": 100,
        "enabled_periods": [1],
        "prices": {"rub": {"1": 100}},
        "hwid_device_limit": 5,
    }


def _config(*tariffs: dict) -> TariffsConfig:
    return TariffsConfig.model_validate(
        {
            "default_tariff": tariffs[0]["key"],
            "default_currency": "rub",
            "tariffs": list(tariffs),
        }
    )


def _subscription(
    subscription_id: int,
    user_id: int,
    tariff_key: str | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        subscription_id=subscription_id,
        user_id=user_id,
        tariff_key=tariff_key,
        tariff_binding_source=None,
        tariff_bound_at=None,
        tariff_binding_note=None,
        tier_baseline_bytes=None,
        traffic_limit_bytes=50,
        topup_balance_bytes=0,
        premium_baseline_bytes=0,
        premium_topup_balance_bytes=0,
        premium_topup_used_bytes=0,
        premium_used_bytes=0,
    )


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _RowsResult:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


def _session(subscriptions, payment_rows=()):
    session = SimpleNamespace()
    session.execute = AsyncMock(
        side_effect=[
            _ScalarResult(subscriptions),
            _RowsResult(payment_rows),
        ]
    )
    session.flush = AsyncMock()
    return session


class TariffReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dry_run_uses_successful_payment_evidence_without_mutating(self) -> None:
        standard = _tariff("standard")
        premium = _tariff("premium")
        subscription = _subscription(1, 42, None)
        session = _session([subscription], [(42, "premium", 99)])

        report = await reconcile_subscription_tariffs(
            session,
            _config(standard, premium),
            apply=False,
        )

        self.assertEqual(report.candidates, 1)
        self.assertEqual(report.applied, 0)
        self.assertEqual(report.unresolved, 0)
        self.assertEqual(report.items[0].proposed_tariff_key, "premium")
        self.assertEqual(report.items[0].source, "payment_history")
        self.assertIsNone(subscription.tariff_key)
        session.flush.assert_not_awaited()

    async def test_apply_binds_single_tariff_and_records_provenance(self) -> None:
        subscription = _subscription(1, 42, None)
        session = _session([subscription])

        report = await reconcile_subscription_tariffs(
            session,
            _config(_tariff("standard")),
            apply=True,
        )

        self.assertEqual(report.applied, 1)
        self.assertEqual(subscription.tariff_key, "standard")
        self.assertEqual(
            subscription.tariff_binding_source,
            "single_tariff_reconciliation",
        )
        self.assertIsNotNone(subscription.tariff_bound_at)
        self.assertEqual(subscription.tier_baseline_bytes, 50)
        session.flush.assert_awaited_once()

    async def test_canonicalizes_legacy_key_even_with_multiple_tariffs(self) -> None:
        subscription = _subscription(1, 42, "old-standard")
        session = _session([subscription])

        report = await reconcile_subscription_tariffs(
            session,
            _config(
                _tariff("standard", legacy_keys=["old-standard"]),
                _tariff("premium"),
            ),
            apply=True,
        )

        self.assertEqual(report.applied, 1)
        self.assertEqual(subscription.tariff_key, "standard")
        self.assertEqual(subscription.tariff_binding_source, "legacy_key")

    async def test_does_not_guess_between_multiple_tariffs(self) -> None:
        subscription = _subscription(1, 42, None)
        session = _session([subscription])

        report = await reconcile_subscription_tariffs(
            session,
            _config(_tariff("standard"), _tariff("premium")),
            apply=True,
        )

        self.assertEqual(report.applied, 0)
        self.assertEqual(report.unresolved, 1)
        self.assertEqual(report.items[0].reason, "ambiguous_missing_tariff")
        self.assertIsNone(subscription.tariff_key)
        session.flush.assert_not_awaited()

    async def test_does_not_fall_back_to_older_payment_when_latest_tariff_is_invalid(
        self,
    ) -> None:
        subscription = _subscription(1, 42, None)
        session = _session(
            [subscription],
            [
                (42, "removed-tariff", 100),
                (42, "premium", 99),
            ],
        )

        report = await reconcile_subscription_tariffs(
            session,
            _config(_tariff("standard"), _tariff("premium")),
            apply=True,
        )

        self.assertEqual(report.applied, 0)
        self.assertEqual(report.unresolved, 1)
        self.assertEqual(report.items[0].reason, "ambiguous_missing_tariff")
        self.assertIsNone(subscription.tariff_key)

    async def test_does_not_apply_user_level_payment_to_multiple_active_rows(self) -> None:
        first = _subscription(1, 42, None)
        second = _subscription(2, 42, None)
        session = _session([first, second], [(42, "standard", 99)])

        report = await reconcile_subscription_tariffs(
            session,
            _config(_tariff("standard"), _tariff("premium")),
            apply=True,
        )

        self.assertEqual(report.applied, 0)
        self.assertEqual(report.unresolved, 2)
        self.assertTrue(
            all(item.reason == "multiple_active_subscriptions" for item in report.items)
        )

    async def test_already_canonical_binding_is_idempotent(self) -> None:
        subscription = _subscription(1, 42, "standard")
        session = _session([subscription])

        report = await reconcile_subscription_tariffs(
            session,
            _config(_tariff("standard")),
            apply=True,
        )

        self.assertEqual(report.healthy, 1)
        self.assertEqual(report.applied, 0)
        self.assertEqual(report.unresolved, 0)
        session.flush.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
