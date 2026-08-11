from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.services.partner_commission_service import PartnerCommissionService
from bot.services.partner_common import (
    PartnerError,
    amount_to_minor,
    commission_minor,
    minor_to_decimal_string,
)
from bot.services.partner_program_service import PartnerProgramService
from bot.services.partner_withdrawal_service import (
    PartnerWithdrawalService,
    decrypt_partner_requisites,
    encrypt_partner_requisites,
)
from config.settings import Settings
from config.settings_models import (
    PartnerSettings,
    PartnerWithdrawalField,
    PartnerWithdrawalMethod,
    PartnerWithdrawalNetwork,
)
from db.models import Payment


@pytest.mark.parametrize(
    ("amount", "scale", "expected"),
    [
        ("12.5", 0, 13),
        ("12.345", 2, 1235),
        ("0.0000005", 6, 1),
        ("999999999999999999.999999", 6, 999999999999999999999999),
    ],
)
def test_partner_minor_units_use_decimal_half_up(
    amount: str,
    scale: int,
    expected: int,
) -> None:
    assert amount_to_minor(amount, scale=scale) == expected
    assert Decimal(minor_to_decimal_string(expected, scale=scale)) == (
        Decimal(expected) / (Decimal(10) ** scale)
    )


@pytest.mark.parametrize(
    ("gross_minor", "bps", "expected"),
    [(1, 5000, 1), (10_001, 3333, 3333), (10**24, 10_000, 10**24), (123, 0, 0)],
)
def test_partner_commission_uses_basis_points_and_half_up(
    gross_minor: int,
    bps: int,
    expected: int,
) -> None:
    assert commission_minor(gross_minor, bps) == expected


def test_partner_requisites_encryption_round_trip_and_wrong_key() -> None:
    old_key = secrets.token_urlsafe(32)
    new_key = secrets.token_urlsafe(32)
    associated_data = b"partner:7:test-request"
    payload = {"card_number": "4111111111111111", "holder": "Test User"}

    ciphertext = encrypt_partner_requisites(
        payload,
        raw_key=old_key,
        associated_data=associated_data,
    )

    assert payload["card_number"].encode() not in ciphertext
    assert (
        decrypt_partner_requisites(
            ciphertext,
            raw_key=old_key,
            associated_data=associated_data,
        )
        == payload
    )
    with pytest.raises(PartnerError, match="withdrawal_requisites_unavailable"):
        decrypt_partner_requisites(
            ciphertext,
            raw_key=new_key,
            associated_data=associated_data,
        )
    with pytest.raises(PartnerError, match="withdrawal_requisites_unavailable"):
        decrypt_partner_requisites(
            ciphertext,
            raw_key=old_key,
            associated_data=b"partner:8:test-request",
        )


def _withdrawal_service() -> PartnerWithdrawalService:
    settings = SimpleNamespace(
        PARTNER_REQUISITES_ENCRYPTION_KEY=SecretStr(secrets.token_urlsafe(32)),
        PARTNER_REQUISITES_KEY_ID="v1",
        partner_settings=PartnerSettings(),
    )
    return PartnerWithdrawalService(settings)  # type: ignore[arg-type]


def test_partner_requisites_are_normalized_and_masked() -> None:
    service = _withdrawal_service()
    card = PartnerWithdrawalMethod(
        id="card",
        type="bank_card",
        label="Card",
        debit_currency="RUB",
        min_amount_minor=10_000,
        fields=[PartnerWithdrawalField(id="card_number")],
    )
    sbp = PartnerWithdrawalMethod(
        id="sbp",
        type="sbp",
        label="SBP",
        debit_currency="RUB",
        min_amount_minor=10_000,
        fields=[PartnerWithdrawalField(id="phone")],
    )
    crypto = PartnerWithdrawalMethod(
        id="usdt",
        type="crypto",
        label="USDT",
        debit_currency="USD",
        min_amount_minor=1000,
        settlement_asset="USDT",
        fields=[PartnerWithdrawalField(id="address")],
        networks=[PartnerWithdrawalNetwork(id="tron", label="TRON")],
    )

    card_values, card_mask = service._validated_requisites(
        card, {"card_number": "4111 1111 1111 1111"}, None
    )
    phone_values, phone_mask = service._validated_requisites(
        sbp, {"phone": "8 (999) 123-45-67"}, None
    )
    crypto_values, crypto_mask = service._validated_requisites(
        crypto, {"address": "TExampleAddress123456789"}, "TRON"
    )

    assert card_values == {"card_number": "4111111111111111"}
    assert card_mask == "•••• 1111"
    assert phone_values == {"phone": "+79991234567"}
    assert phone_mask == "+79••••4567"
    assert crypto_values["network"] == "tron"
    assert crypto_mask.endswith("(tron)")


def test_enabled_withdrawal_method_requires_canonical_field() -> None:
    with pytest.raises(ValidationError, match="card_number"):
        PartnerWithdrawalMethod(
            id="card",
            type="bank_card",
            debit_currency="RUB",
            min_amount_minor=100,
        )


def _partner_settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "enabled": True,
        "eligible_currencies": ["RUB"],
        "excluded_sale_modes": ["traffic_topup"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _commission_service(**overrides: object) -> PartnerCommissionService:
    settings = SimpleNamespace(partner_settings=_partner_settings(**overrides))
    return PartnerCommissionService(settings)  # type: ignore[arg-type]


def _payment(**overrides: object) -> Payment:
    values: dict[str, object] = {
        "amount": 100,
        "currency": "RUB",
        "provider": "test",
        "sale_mode": "subscription",
        "funding_source": "external",
    }
    values.update(overrides)
    return Payment(**values)


@pytest.mark.parametrize(
    ("payment", "profile_status", "eligible_from_offset", "expected"),
    [
        (_payment(funding_source="partner_balance"), "active", -1, "internal_funding_source"),
        (_payment(currency="USD"), "active", -1, "currency_not_eligible"),
        (_payment(sale_mode="traffic_topup"), "active", -1, "sale_mode_excluded"),
        (_payment(), "paused", -1, "partner_paused"),
        (_payment(), "active", 1, "before_attribution"),
        (_payment(), "active", -1, None),
    ],
)
def test_partner_commission_exclusion_matrix(
    payment: Payment,
    profile_status: str,
    eligible_from_offset: int,
    expected: str | None,
) -> None:
    paid_at = datetime.now(UTC)
    profile = SimpleNamespace(status=profile_status)
    result = _commission_service()._exclusion_reason(
        payment=payment,
        profile=profile,  # type: ignore[arg-type]
        eligible_from=paid_at + timedelta(seconds=eligible_from_offset),
        source_paid_at=paid_at,
    )
    assert result == expected


def test_partner_attribution_is_first_touch(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = SimpleNamespace(partner_client_id=17, partner_id=3)
    get_existing = AsyncMock(return_value=existing)
    get_profile = AsyncMock()
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_client_by_user_id",
        get_existing,
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_profile_by_code",
        get_profile,
    )
    service = PartnerProgramService(
        SimpleNamespace(partner_settings=_partner_settings())  # type: ignore[arg-type]
    )

    async def run() -> object:
        return await service.attribute_user(
            AsyncMock(),
            user=SimpleNamespace(user_id=5),  # type: ignore[arg-type]
            partner_code="second-link",
            source="partner_web_link",
        )

    result = asyncio.run(run())

    assert result is existing
    get_profile.assert_not_awaited()


@pytest.mark.parametrize(("enabled", "snapshotted"), [(True, True), (False, False)])
def test_partner_registration_snapshots_welcome_toggle_at_first_touch(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    snapshotted: bool,
) -> None:
    attribution = SimpleNamespace(
        partner_client_id=17,
        partner_id=3,
        attributed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    create_attribution = AsyncMock(return_value=attribution)
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_client_by_user_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_profile_by_code",
        AsyncMock(
            return_value=SimpleNamespace(
                partner_id=3,
                user_id=9,
                status="active",
            )
        ),
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.create_client_attribution",
        create_attribution,
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.create_audit_event",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.events.emit_model",
        AsyncMock(),
    )
    service = PartnerProgramService(
        SimpleNamespace(
            partner_settings=_partner_settings(
                client_welcome_bonus_enabled=enabled,
            )
        )
    )
    session = AsyncMock(spec=AsyncSession)
    session.begin_nested.return_value = AsyncMock()
    user = SimpleNamespace(
        user_id=5,
        first_name="Client",
        last_name=None,
        username=None,
        email=None,
    )

    result = asyncio.run(
        service.attribute_user(
            session,
            user=user,
            partner_code="first-link",
            source="partner_web_link",
            registered_via_partner_link=True,
        )
    )

    assert result is attribution
    create_call = create_attribution.await_args
    assert create_call is not None
    eligible_at = create_call.kwargs["welcome_bonus_eligible_at"]
    assert (eligible_at is not None) is snapshotted


@pytest.mark.parametrize(
    ("eligible_at", "profile_status", "expected"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC), "active", True),
        (None, "active", False),
        (datetime(2026, 1, 1, tzinfo=UTC), "paused", False),
    ],
)
def test_partner_welcome_bonus_requires_registration_snapshot_and_active_profile(
    monkeypatch: pytest.MonkeyPatch,
    eligible_at: datetime | None,
    profile_status: str,
    expected: bool,
) -> None:
    get_attribution = AsyncMock(
        return_value=(
            SimpleNamespace(
                source="partner_web_link",
                welcome_bonus_eligible_at=eligible_at,
            ),
            SimpleNamespace(status=profile_status),
        )
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_client_with_profile_for_user",
        get_attribution,
    )
    service = PartnerProgramService(
        SimpleNamespace(
            partner_settings=_partner_settings(
                client_welcome_bonus_enabled=True,
            )
        )
    )

    result = asyncio.run(
        service.client_welcome_bonus_eligible(
            AsyncMock(),
            user_id=5,
        )
    )

    assert result is expected


def test_partner_client_benefits_stay_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    get_attribution = AsyncMock()
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_client_with_profile_for_user",
        get_attribution,
    )
    service = PartnerProgramService(
        cast(Settings, SimpleNamespace(partner_settings=PartnerSettings(enabled=True)))
    )

    welcome = asyncio.run(
        service.client_welcome_bonus_eligible(
            AsyncMock(),
            user_id=5,
        )
    )
    payment = asyncio.run(
        service.client_payment_bonus_eligible(
            AsyncMock(),
            user_id=5,
        )
    )

    assert welcome is False
    assert payment is False
    get_attribution.assert_not_awaited()


@pytest.mark.parametrize(
    (
        "referral_enabled",
        "program_enabled",
        "referrals_disabled",
        "profile",
        "expected",
        "queried",
    ),
    [
        (False, False, False, None, False, False),
        (True, False, True, SimpleNamespace(status="active"), True, False),
        (True, True, False, SimpleNamespace(status="active"), True, False),
        (True, True, True, None, True, True),
        (True, True, True, SimpleNamespace(status="active"), False, True),
        (True, True, True, SimpleNamespace(status="paused"), False, True),
        (True, True, True, SimpleNamespace(status="closed"), False, True),
    ],
)
def test_partner_profile_controls_referral_program_visibility(
    monkeypatch: pytest.MonkeyPatch,
    referral_enabled: bool,
    program_enabled: bool,
    referrals_disabled: bool,
    profile: object | None,
    expected: bool,
    queried: bool,
) -> None:
    get_profile = AsyncMock(return_value=profile)
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_profile_by_user_id",
        get_profile,
    )
    service = PartnerProgramService(
        SimpleNamespace(
            REFERRAL_PROGRAM_ENABLED=referral_enabled,
            referral_settings=SimpleNamespace(enabled=referral_enabled),
            partner_settings=_partner_settings(
                enabled=program_enabled,
                referral_program_disabled=referrals_disabled,
            ),
        )  # type: ignore[arg-type]
    )

    result = asyncio.run(
        service.referral_program_enabled_for_user(
            AsyncMock(),
            user_id=5,
        )
    )

    assert result is expected
    if queried:
        get_profile.assert_awaited_once()
    else:
        get_profile.assert_not_awaited()


@pytest.mark.parametrize(("application_id", "emitted"), [(None, True), (17, False)])
def test_direct_partner_activation_emits_status_event_once(
    monkeypatch: pytest.MonkeyPatch,
    application_id: int | None,
    emitted: bool,
) -> None:
    activated_at = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
    profile = SimpleNamespace(
        partner_id=8,
        user_id=42,
        status="active",
        commission_bps=3000,
        activated_at=activated_at,
    )
    emit_model = AsyncMock()
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_profile_by_user_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.create_profile",
        AsyncMock(return_value=profile),
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.create_audit_event",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.events.emit_model",
        emit_model,
    )
    service = PartnerProgramService(
        SimpleNamespace(
            partner_settings=_partner_settings(default_commission_bps=3000),
        )  # type: ignore[arg-type]
    )
    session = AsyncMock(spec=AsyncSession)
    session.begin_nested.return_value = AsyncMock()
    user = SimpleNamespace(
        user_id=42,
        first_name="Alice",
        last_name=None,
        username="alice",
        email=None,
    )

    asyncio.run(
        service.create_profile_for_user(
            session,
            user=user,  # type: ignore[arg-type]
            actor_admin_id=1,
            application_id=application_id,
        )
    )

    if emitted:
        emit_model.assert_awaited_once()
        emit_call = emit_model.await_args
        assert emit_call is not None
        payload = emit_call.args[0]
        assert payload.partner_id == 8
        assert payload.user_id == 42
        assert payload.old_status == "none"
        assert payload.status == "active"
        assert payload.changed_at == activated_at
    else:
        emit_model.assert_not_awaited()


def test_partner_self_attribution_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_client_by_user_id",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "bot.services.partner_program_service.partner_dal.get_profile_by_code",
        AsyncMock(
            return_value=SimpleNamespace(
                partner_id=9,
                user_id=5,
                status="active",
            )
        ),
    )
    service = PartnerProgramService(
        SimpleNamespace(partner_settings=_partner_settings())  # type: ignore[arg-type]
    )

    async def run() -> object:
        return await service.attribute_user(
            AsyncMock(),
            user=SimpleNamespace(user_id=5),  # type: ignore[arg-type]
            partner_code="own-link",
            source="partner_telegram_link",
        )

    with pytest.raises(PartnerError, match="partner_self_attribution"):
        asyncio.run(run())
