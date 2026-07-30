from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from bot.services.subscription_service_impl.entitlement_helpers import (
    immutable_subscription_start,
)


def test_immutable_subscription_start_preserves_historical_value() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    original_start = now - timedelta(days=180)

    actual = immutable_subscription_start(
        SimpleNamespace(start_date=original_start),
        now=now,
    )

    assert actual == original_start


def test_immutable_subscription_start_rejects_future_value() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)

    actual = immutable_subscription_start(
        SimpleNamespace(start_date=now + timedelta(days=3)),
        now=now,
    )

    assert actual == now


def test_immutable_subscription_start_normalizes_naive_utc_value() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    naive_start = datetime(2026, 1, 1, 8, tzinfo=UTC).replace(tzinfo=None)

    actual = immutable_subscription_start(
        SimpleNamespace(start_date=naive_start),
        now=now,
    )

    assert actual == naive_start.replace(tzinfo=UTC)
