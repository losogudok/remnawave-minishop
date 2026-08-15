from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.payment_providers.shared import RecurringChargeContext
from bot.payment_providers.shared.durable_recurring import (
    prepare_durable_recurring_charge,
)
from db.dal import auto_renew_dal, payment_dal


def _context(
    session: AsyncMock,
    *,
    auto_renew_cycle_id: int | None = None,
) -> RecurringChargeContext:
    return RecurringChargeContext(
        session=session,
        user_id=42,
        subscription_id=7,
        saved_method=SimpleNamespace(provider_payment_method_id="token-1"),
        amount=199.0,
        currency="RUB",
        months=1,
        sale_mode="subscription@standard",
        description="Auto-renewal",
        metadata={"subscription_id": "7"},
        idempotence_key="renewal-cycle-7",
        renewal_cycle_end=datetime(2030, 1, 15, tzinfo=UTC),
        consent_version=3,
        payment_method_db_id=9,
        auto_renew_cycle_id=auto_renew_cycle_id,
    )


async def _prepare_persists_cycle_payment_and_dispatch_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    context = _context(session)
    payment = SimpleNamespace(payment_id=91, provider_payment_id=None, status="pending_stripe")

    async def create_cycle(_session: object, payload: dict[str, object]):
        return (
            SimpleNamespace(
                cycle_id=11,
                subscription_id=7,
                user_id=42,
                provider="stripe",
                base_idempotence_key="renewal-cycle-7",
                consent_version=3,
                payment_method_id=9,
                payment_method_provider_id="token-1",
                request_snapshot=payload["request_snapshot"],
                state="scheduled",
                stopped_reason=None,
                current_payment_id=None,
                transport_replays=0,
                financial_attempts=1,
            ),
            True,
        )

    create_cycle_mock = AsyncMock(side_effect=create_cycle)
    # Simulate a process restart after the idempotent payment row committed but
    # before the cycle dispatch intent was linked. The same payment must be
    # dispatched, not mistaken for an already-started provider request.
    create_payment_mock = AsyncMock(return_value=(payment, False))
    prepare_payment_mock = AsyncMock(return_value=payment)
    record_dispatch_mock = AsyncMock()
    monkeypatch.setattr(auto_renew_dal, "create_or_get_cycle", create_cycle_mock)
    monkeypatch.setattr(
        payment_dal,
        "create_or_get_payment_record_by_idempotence_key",
        create_payment_mock,
    )
    monkeypatch.setattr(auto_renew_dal, "prepare_payment_dispatch", prepare_payment_mock)
    monkeypatch.setattr(auto_renew_dal, "record_payment_dispatch", record_dispatch_mock)
    monkeypatch.setattr(
        auto_renew_dal,
        "validate_dispatch_context_for_update",
        AsyncMock(return_value=True),
    )

    preparation = await prepare_durable_recurring_charge(
        context,
        provider="stripe",
        saved_method_id="token-1",
        pending_status="pending_stripe",
        max_transport_replays=4,
        lease_seconds=60,
    )

    assert preparation.result is None
    assert preparation.dispatch is not None
    assert preparation.dispatch.payment_id == 91
    assert preparation.dispatch.request_id == "renewal-cycle-7"
    create_payment_call = create_payment_mock.await_args
    assert create_payment_call is not None
    payment_payload = create_payment_call.args[1]
    assert payment_payload["provider"] == "stripe"
    assert payment_payload["idempotence_key"] == "renewal-cycle-7"
    assert payment_payload["auto_renew_cycle_id"] == 11
    prepare_payment_mock.assert_awaited_once()
    record_dispatch_mock.assert_awaited_once()
    assert session.commit.await_count == 2


def test_prepare_persists_cycle_payment_and_dispatch_before_provider_call() -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        asyncio.run(_prepare_persists_cycle_payment_and_dispatch_before_provider_call(monkeypatch))
    finally:
        monkeypatch.undo()


async def _existing_pending_cycle_is_returned_without_a_second_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    context = _context(session, auto_renew_cycle_id=11)
    cycle = SimpleNamespace(
        cycle_id=11,
        subscription_id=7,
        user_id=42,
        provider="stripe",
        base_idempotence_key="renewal-cycle-7",
        consent_version=3,
        payment_method_id=9,
        payment_method_provider_id="token-1",
        request_snapshot="",
        state="waiting_provider",
        stopped_reason=None,
        current_payment_id=91,
        transport_replays=0,
        financial_attempts=1,
    )
    payment = SimpleNamespace(
        payment_id=91,
        provider_payment_id="pi_1",
        status="pending_stripe",
    )

    async def load_cycle(*_args: object, **_kwargs: object):
        snapshot_context = _context(session)
        from bot.payment_providers.shared import RecurringRequestSnapshot

        snapshot = RecurringRequestSnapshot(
            amount=snapshot_context.amount,
            currency=snapshot_context.currency,
            months=snapshot_context.months,
            sale_mode=snapshot_context.sale_mode,
            description=snapshot_context.description,
            metadata=dict(snapshot_context.metadata),
            hwid_quote=None,
            entitlement_context_snapshot=None,
            checkout_bundle_snapshot=None,
        )
        cycle.request_snapshot = snapshot.to_json()
        return cycle

    monkeypatch.setattr(auto_renew_dal, "get_cycle", AsyncMock(side_effect=load_cycle))
    monkeypatch.setattr(payment_dal, "get_payment_by_db_id", AsyncMock(return_value=payment))
    create_payment_mock = AsyncMock()
    record_dispatch_mock = AsyncMock()
    monkeypatch.setattr(
        payment_dal,
        "create_or_get_payment_record_by_idempotence_key",
        create_payment_mock,
    )
    monkeypatch.setattr(auto_renew_dal, "record_payment_dispatch", record_dispatch_mock)

    preparation = await prepare_durable_recurring_charge(
        context,
        provider="stripe",
        saved_method_id="token-1",
        pending_status="pending_stripe",
        max_transport_replays=4,
        lease_seconds=60,
    )

    assert preparation.dispatch is None
    assert preparation.result is not None
    assert preparation.result.initiated is True
    assert preparation.result.payment_db_id == 91
    assert preparation.result.provider_payment_id == "pi_1"
    create_payment_mock.assert_not_awaited()
    record_dispatch_mock.assert_not_awaited()


def test_existing_pending_cycle_is_returned_without_a_second_dispatch() -> None:
    monkeypatch = pytest.MonkeyPatch()
    try:
        asyncio.run(_existing_pending_cycle_is_returned_without_a_second_dispatch(monkeypatch))
    finally:
        monkeypatch.undo()
