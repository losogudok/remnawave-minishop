from __future__ import annotations

from typing import Any

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Payment
from db.partner_models import (
    PartnerClient,
    PartnerCommission,
    PartnerLedgerEntry,
    PartnerWithdrawal,
)


async def _count(session: AsyncSession, statement: Any) -> int:
    return int((await session.execute(statement)).scalar_one() or 0)


async def build_partner_reconciliation_report(session: AsyncSession) -> dict[str, Any]:
    """Build a read-only, PII-free partner financial integrity report."""

    missing_decisions = await _count(
        session,
        select(func.count(Payment.payment_id))
        .select_from(Payment)
        .join(PartnerClient, PartnerClient.client_user_id == Payment.user_id)
        .outerjoin(PartnerCommission, PartnerCommission.payment_id == Payment.payment_id)
        .where(
            Payment.status == "succeeded",
            PartnerCommission.commission_id.is_(None),
        ),
    )

    ledger_refs = (
        select(
            PartnerLedgerEntry.reference_type.label("reference_type"),
            PartnerLedgerEntry.reference_id.label("reference_id"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            PartnerLedgerEntry.state != "void",
                            PartnerLedgerEntry.amount_minor,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("net_minor"),
        )
        .group_by(PartnerLedgerEntry.reference_type, PartnerLedgerEntry.reference_id)
        .subquery()
    )

    commission_expected = case(
        (
            PartnerCommission.status.in_(("pending", "available")),
            PartnerCommission.commission_amount_minor,
        ),
        else_=0,
    )
    commission_ledger_mismatches = await _count(
        session,
        select(func.count(PartnerCommission.commission_id))
        .select_from(PartnerCommission)
        .outerjoin(
            ledger_refs,
            and_(
                ledger_refs.c.reference_type == "commission",
                ledger_refs.c.reference_id == cast(PartnerCommission.commission_id, String),
            ),
        )
        .where(func.coalesce(ledger_refs.c.net_minor, 0) != commission_expected),
    )

    withdrawal_expected = case(
        (
            PartnerWithdrawal.status.in_(("requested", "processing", "paid")),
            -PartnerWithdrawal.debit_amount_minor,
        ),
        else_=0,
    )
    withdrawal_ledger_mismatches = await _count(
        session,
        select(func.count(PartnerWithdrawal.withdrawal_id))
        .select_from(PartnerWithdrawal)
        .outerjoin(
            ledger_refs,
            and_(
                ledger_refs.c.reference_type == "withdrawal",
                ledger_refs.c.reference_id == cast(PartnerWithdrawal.withdrawal_id, String),
            ),
        )
        .where(func.coalesce(ledger_refs.c.net_minor, 0) != withdrawal_expected),
    )

    duplicate_reference_groups = (
        select(
            PartnerLedgerEntry.reference_type,
            PartnerLedgerEntry.reference_id,
            PartnerLedgerEntry.kind,
        )
        .where(PartnerLedgerEntry.kind != "manual_adjustment")
        .group_by(
            PartnerLedgerEntry.reference_type,
            PartnerLedgerEntry.reference_id,
            PartnerLedgerEntry.kind,
        )
        .having(func.count(PartnerLedgerEntry.entry_id) > 1)
        .subquery()
    )
    duplicate_ledger_references = await _count(
        session,
        select(func.count()).select_from(duplicate_reference_groups),
    )

    orphan_commission_references = await _count(
        session,
        select(func.count(PartnerLedgerEntry.entry_id))
        .select_from(PartnerLedgerEntry)
        .outerjoin(
            PartnerCommission,
            and_(
                PartnerLedgerEntry.reference_type == "commission",
                PartnerLedgerEntry.reference_id == cast(PartnerCommission.commission_id, String),
            ),
        )
        .where(
            PartnerLedgerEntry.reference_type == "commission",
            PartnerCommission.commission_id.is_(None),
        ),
    )
    orphan_withdrawal_references = await _count(
        session,
        select(func.count(PartnerLedgerEntry.entry_id))
        .select_from(PartnerLedgerEntry)
        .outerjoin(
            PartnerWithdrawal,
            and_(
                PartnerLedgerEntry.reference_type == "withdrawal",
                PartnerLedgerEntry.reference_id == cast(PartnerWithdrawal.withdrawal_id, String),
            ),
        )
        .where(
            PartnerLedgerEntry.reference_type == "withdrawal",
            PartnerWithdrawal.withdrawal_id.is_(None),
        ),
    )
    orphan_payment_references = await _count(
        session,
        select(func.count(PartnerLedgerEntry.entry_id))
        .select_from(PartnerLedgerEntry)
        .outerjoin(
            Payment,
            and_(
                PartnerLedgerEntry.reference_type == "payment",
                PartnerLedgerEntry.reference_id == cast(Payment.payment_id, String),
            ),
        )
        .where(
            PartnerLedgerEntry.reference_type == "payment",
            Payment.payment_id.is_(None),
        ),
    )
    empty_idempotency_keys = await _count(
        session,
        select(func.count(PartnerLedgerEntry.entry_id)).where(
            or_(
                PartnerLedgerEntry.idempotency_key.is_(None),
                func.length(func.trim(PartnerLedgerEntry.idempotency_key)) == 0,
            )
        ),
    )

    balance_rows = (
        await session.execute(
            select(
                PartnerLedgerEntry.partner_id,
                PartnerLedgerEntry.currency,
                PartnerLedgerEntry.currency_scale,
                func.coalesce(func.sum(PartnerLedgerEntry.amount_minor), 0).label("balance_minor"),
            )
            .where(PartnerLedgerEntry.state == "posted")
            .group_by(
                PartnerLedgerEntry.partner_id,
                PartnerLedgerEntry.currency,
                PartnerLedgerEntry.currency_scale,
            )
            .order_by(PartnerLedgerEntry.partner_id, PartnerLedgerEntry.currency)
        )
    ).all()
    balances: list[dict[str, Any]] = [
        {
            "partner_id": int(row.partner_id),
            "currency": str(row.currency).upper(),
            "currency_scale": int(row.currency_scale),
            "balance_minor": int(row.balance_minor or 0),
        }
        for row in balance_rows
    ]
    negative_balances = [item for item in balances if int(item["balance_minor"]) < 0]
    issue_counts = {
        "missing_decisions": missing_decisions,
        "commission_ledger_mismatches": commission_ledger_mismatches,
        "withdrawal_ledger_mismatches": withdrawal_ledger_mismatches,
        "duplicate_ledger_references": duplicate_ledger_references,
        "orphan_commission_references": orphan_commission_references,
        "orphan_withdrawal_references": orphan_withdrawal_references,
        "orphan_payment_references": orphan_payment_references,
        "empty_idempotency_keys": empty_idempotency_keys,
    }
    return {
        "ok": all(value == 0 for value in issue_counts.values()),
        "issues": issue_counts,
        "balance_rows": balances,
        "negative_balances": negative_balances,
    }


__all__ = ["build_partner_reconciliation_report"]
