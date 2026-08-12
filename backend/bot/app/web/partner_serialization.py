from __future__ import annotations

import json
from typing import Any

from bot.app.web.partner_schemas import (
    PartnerApplicationOut,
    PartnerBalanceOut,
    PartnerClientOut,
    PartnerCommissionOut,
    PartnerProfileOut,
    PartnerWithdrawalOut,
)
from db.partner_models import (
    PartnerApplication,
    PartnerClient,
    PartnerCommission,
    PartnerProfile,
    PartnerWithdrawal,
)


def application_out(
    application: PartnerApplication,
    *,
    include_message: bool = True,
) -> PartnerApplicationOut:
    return PartnerApplicationOut(
        application_id=int(application.application_id),
        user_id=int(application.user_id) if application.user_id is not None else None,
        display_label=str(application.display_label_snapshot),
        message=str(application.message) if include_message else None,
        status=str(application.status),
        submitted_at=application.submitted_at,
        decided_at=application.decided_at,
        decision_message=application.decision_message,
        approved_commission_bps=application.approved_commission_bps,
        welcome_message=application.welcome_message,
        reapply_allowed_at=application.reapply_allowed_at,
    )


def profile_out(profile: PartnerProfile) -> PartnerProfileOut:
    return PartnerProfileOut(
        partner_id=int(profile.partner_id),
        user_id=int(profile.user_id) if profile.user_id is not None else None,
        display_label=str(profile.display_label_snapshot),
        status=str(profile.status),
        commission_bps=int(profile.commission_bps),
        welcome_message=profile.welcome_message,
        pause_reason=profile.pause_reason,
        activated_at=profile.activated_at,
        created_at=profile.created_at,
    )


def balance_out(value: dict[str, Any]) -> PartnerBalanceOut:
    return PartnerBalanceOut.model_validate(value)


def client_out(
    client: PartnerClient,
    *,
    payments_count: int,
    gross_minor: int,
    currency: str | None,
    currency_scale: int,
) -> PartnerClientOut:
    return PartnerClientOut(
        partner_client_id=int(client.partner_client_id),
        public_client_id=str(client.public_client_id),
        label=str(client.public_label_snapshot),
        source=str(client.source),
        attributed_at=client.attributed_at,
        eligible_from=client.eligible_from,
        payments_count=payments_count,
        gross_minor=gross_minor,
        currency=currency,
        currency_scale=currency_scale,
    )


def commission_out(
    commission: PartnerCommission,
    client: PartnerClient,
) -> PartnerCommissionOut:
    return PartnerCommissionOut(
        commission_id=int(commission.commission_id),
        payment_id=int(commission.payment_id) if commission.payment_id is not None else None,
        client_public_id=str(client.public_client_id),
        client_label=str(client.public_label_snapshot),
        gross_amount_minor=int(commission.gross_amount_minor),
        commission_amount_minor=int(commission.commission_amount_minor),
        currency=str(commission.currency),
        currency_scale=int(commission.currency_scale),
        commission_bps=int(commission.commission_bps_snapshot),
        sale_mode=commission.sale_mode_snapshot,
        provider=commission.provider_snapshot,
        status=str(commission.status),
        exclusion_reason=commission.exclusion_reason,
        source_paid_at=commission.source_paid_at,
        available_at=commission.available_at,
        created_at=commission.created_at,
        reversed_at=commission.reversed_at,
    )


def _withdrawal_masked_requisites(
    withdrawal: PartnerWithdrawal,
    method_snapshot: dict[str, Any],
) -> str:
    masked = str(withdrawal.masked_requisites)
    if str(withdrawal.method_type_snapshot) != "crypto":
        return masked
    network_id = str(withdrawal.network or "").strip().lower()
    if not network_id:
        return masked
    network_label = network_id
    networks = method_snapshot.get("networks")
    if isinstance(networks, list):
        for candidate in networks:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("id") or "").strip().lower() != network_id:
                continue
            network_label = str(candidate.get("label") or network_id).strip() or network_id
            break
    legacy_suffix = f" ({network_id})"
    if not masked.lower().endswith(legacy_suffix.lower()) or "…" not in masked:
        return masked
    address_tail = masked[: -len(legacy_suffix)].rsplit("…", 1)[-1].strip()
    if not address_tail:
        return network_label
    return f"{network_label} · ••••{address_tail[-8:]}"


def withdrawal_out(withdrawal: PartnerWithdrawal) -> PartnerWithdrawalOut:
    try:
        method_snapshot = json.loads(str(withdrawal.method_snapshot_json or "{}"))
    except (TypeError, ValueError):
        method_snapshot = {}
    return PartnerWithdrawalOut(
        withdrawal_id=int(withdrawal.withdrawal_id),
        partner_id=int(withdrawal.partner_id),
        method_id=str(withdrawal.method_id_snapshot),
        method_type=str(withdrawal.method_type_snapshot),
        method_snapshot=method_snapshot if isinstance(method_snapshot, dict) else {},
        amount_minor=int(withdrawal.debit_amount_minor),
        currency=str(withdrawal.debit_currency),
        currency_scale=int(withdrawal.currency_scale),
        settlement_asset=withdrawal.settlement_asset,
        network=withdrawal.network,
        status=str(withdrawal.status),
        status_version=int(withdrawal.status_version),
        status_message=withdrawal.status_message,
        external_reference=withdrawal.external_reference,
        settlement_amount=withdrawal.settlement_amount,
        masked_requisites=_withdrawal_masked_requisites(withdrawal, method_snapshot),
        requested_at=withdrawal.requested_at,
        processing_at=withdrawal.processing_at,
        paid_at=withdrawal.paid_at,
        decided_at=withdrawal.decided_at,
    )
