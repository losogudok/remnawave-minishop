# SQLAlchemy legacy Column declarations expose instance attributes as Column[T]
# to mypy; this service intentionally mutates loaded ORM instances.
# mypy: disable-error-code="assignment,arg-type"

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.infra import events
from bot.infra.event_payloads import (
    PartnerBalanceAdjustedPayload,
    PartnerBalanceSpentPayload,
    PartnerCommissionAvailablePayload,
    PartnerCommissionRecordedPayload,
    PartnerCommissionReversedPayload,
)
from bot.infra.payment_events import sale_mode_base
from bot.services.partner_common import (
    PartnerError,
    amount_to_minor,
    commission_minor,
    compact_json,
    currency_scale,
)
from config.settings import Settings
from db.dal import partner_dal
from db.models import Payment
from db.partner_models import PartnerCommission, PartnerLedgerEntry, PartnerProfile


class PartnerCommissionService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def config(self):
        return self.settings.partner_settings

    def _exclusion_reason(
        self,
        *,
        payment: Payment,
        profile: PartnerProfile,
        eligible_from: datetime,
        source_paid_at: datetime,
    ) -> str | None:
        if not self.config.enabled:
            return "program_disabled"
        if profile.status != "active":
            return f"partner_{profile.status}"
        if source_paid_at < eligible_from:
            return "before_attribution"
        if str(getattr(payment, "funding_source", "external") or "external") != "external":
            return "internal_funding_source"
        if str(payment.provider or "").strip().lower() == "partner_balance":
            return "internal_funding_source"
        currency = str(payment.currency or "").strip().upper()
        if currency not in self.config.eligible_currencies:
            return "currency_not_eligible"
        base = sale_mode_base(payment.sale_mode)
        if base in self.config.excluded_sale_modes:
            return "sale_mode_excluded"
        if Decimal(str(payment.amount or 0)) < 0:
            return "invalid_amount"
        return None

    async def record_payment_decision(
        self,
        session: AsyncSession,
        payment: Payment,
        *,
        source_paid_at: datetime | None = None,
    ) -> PartnerCommission | None:
        payment_id = int(payment.payment_id)
        existing = await partner_dal.get_commission_by_payment_id(session, payment_id)
        if existing:
            return existing
        attribution = await partner_dal.get_client_with_profile_for_user(
            session,
            int(payment.user_id),
            for_update=True,
        )
        if not attribution:
            return None
        client, profile = attribution
        paid_at = source_paid_at or datetime.now(UTC)
        scale = currency_scale(str(payment.currency))
        gross_minor = max(0, amount_to_minor(payment.amount, scale=scale))
        reason = self._exclusion_reason(
            payment=payment,
            profile=profile,
            eligible_from=client.eligible_from,
            source_paid_at=paid_at,
        )
        amount_minor = 0 if reason else commission_minor(gross_minor, int(profile.commission_bps))
        available_at = paid_at + timedelta(days=self.config.commission_hold_days)
        status = (
            "excluded"
            if reason
            else ("pending" if self.config.commission_hold_days > 0 else "available")
        )
        try:
            async with session.begin_nested():
                decision = await partner_dal.create_commission(
                    session,
                    partner_id=int(profile.partner_id),
                    partner_client_id=int(client.partner_client_id),
                    payment_id=payment_id,
                    gross_amount_minor=gross_minor,
                    commission_amount_minor=amount_minor,
                    currency=str(payment.currency).upper(),
                    currency_scale=scale,
                    commission_bps_snapshot=int(profile.commission_bps),
                    sale_mode_snapshot=str(payment.sale_mode or "subscription"),
                    provider_snapshot=str(payment.provider or ""),
                    status=status,
                    exclusion_reason=reason,
                    source_paid_at=paid_at,
                    available_at=available_at,
                )
                if status != "excluded" and amount_minor != 0:
                    await partner_dal.create_ledger_entry(
                        session,
                        partner_id=int(profile.partner_id),
                        currency=str(payment.currency).upper(),
                        currency_scale=scale,
                        amount_minor=amount_minor,
                        kind="commission_credit",
                        state="pending" if status == "pending" else "posted",
                        reference_type="commission",
                        reference_id=str(decision.commission_id),
                        idempotency_key=f"commission:{payment_id}",
                        posted_at=paid_at if status == "available" else None,
                    )
        except IntegrityError:
            existing = await partner_dal.get_commission_by_payment_id(session, payment_id)
            if existing:
                return existing
            raise
        return decision

    @staticmethod
    async def emit_recorded(decision: PartnerCommission | None) -> None:
        if decision is None:
            return
        await events.emit_model(
            PartnerCommissionRecordedPayload(
                partner_id=int(decision.partner_id),
                commission_id=int(decision.commission_id),
                payment_db_id=int(decision.payment_id),
                status=str(decision.status),
                currency=str(decision.currency),
                gross_amount_minor=int(decision.gross_amount_minor),
                commission_amount_minor=int(decision.commission_amount_minor),
                available_at=decision.available_at,
            )
        )

    async def post_due_commissions(
        self,
        session: AsyncSession,
        *,
        limit: int = 200,
    ) -> list[PartnerCommission]:
        now = datetime.now(UTC)
        decisions = await partner_dal.list_pending_commissions(session, now=now, limit=limit)
        posted: list[PartnerCommission] = []
        for decision in decisions:
            entry = await partner_dal.get_ledger_entry_by_key(
                session,
                f"commission:{int(decision.payment_id)}",
            )
            if entry is None and int(decision.commission_amount_minor) != 0:
                entry = await partner_dal.create_ledger_entry(
                    session,
                    partner_id=int(decision.partner_id),
                    currency=str(decision.currency),
                    currency_scale=int(decision.currency_scale),
                    amount_minor=int(decision.commission_amount_minor),
                    kind="commission_credit",
                    state="pending",
                    reference_type="commission",
                    reference_id=str(decision.commission_id),
                    idempotency_key=f"commission:{int(decision.payment_id)}",
                )
            if entry is not None and entry.state == "pending":
                entry.state = "posted"
                entry.posted_at = now
            decision.status = "available"
            posted.append(decision)
        await session.flush()
        return posted

    @staticmethod
    async def emit_available(decision: PartnerCommission) -> None:
        await events.emit_model(
            PartnerCommissionAvailablePayload(
                partner_id=int(decision.partner_id),
                commission_id=int(decision.commission_id),
                currency=str(decision.currency),
                commission_amount_minor=int(decision.commission_amount_minor),
                available_at=decision.available_at,
            )
        )

    @staticmethod
    async def reverse_payment(
        session: AsyncSession,
        payment_id: int,
    ) -> PartnerCommission | None:
        decision = await partner_dal.get_commission_by_payment_id(
            session,
            payment_id,
            for_update=True,
        )
        if decision is None or decision.status in {"reversed", "excluded"}:
            return decision
        now = datetime.now(UTC)
        entry = await partner_dal.get_ledger_entry_by_key(session, f"commission:{payment_id}")
        if entry and entry.state == "pending":
            entry.state = "void"
        elif (
            entry
            and entry.state == "posted"
            and not await partner_dal.get_ledger_entry_by_key(
                session,
                f"commission-reversal:{payment_id}",
            )
        ):
            await partner_dal.create_ledger_entry(
                session,
                partner_id=int(decision.partner_id),
                currency=str(decision.currency),
                currency_scale=int(decision.currency_scale),
                amount_minor=-int(decision.commission_amount_minor),
                kind="commission_reversal",
                state="posted",
                reference_type="commission",
                reference_id=str(decision.commission_id),
                idempotency_key=f"commission-reversal:{payment_id}",
                reason="source payment refunded",
                posted_at=now,
            )
        decision.status = "reversed"
        decision.reversed_at = now
        await session.flush()
        await events.emit_model(
            PartnerCommissionReversedPayload(
                partner_id=int(decision.partner_id),
                commission_id=int(decision.commission_id),
                payment_db_id=int(decision.payment_id) if decision.payment_id else None,
                currency=str(decision.currency),
                commission_amount_minor=int(decision.commission_amount_minor),
                reversed_at=now,
            )
        )
        return decision

    async def reconcile_payments(
        self,
        session: AsyncSession,
        *,
        limit: int = 200,
        dry_run: bool = False,
    ) -> list[PartnerCommission]:
        payments = await partner_dal.list_successful_payments_without_decision(
            session,
            limit=limit,
        )
        if dry_run:
            return []
        decisions: list[PartnerCommission] = []
        for payment in payments:
            source_paid_at = payment.updated_at or payment.created_at or datetime.now(UTC)
            if source_paid_at.tzinfo is None:
                source_paid_at = source_paid_at.replace(tzinfo=UTC)
            decision = await self.record_payment_decision(
                session,
                payment,
                source_paid_at=source_paid_at,
            )
            if decision:
                decisions.append(decision)
        return decisions

    async def reconcile_refunds(
        self,
        session: AsyncSession,
        *,
        limit: int = 200,
    ) -> list[PartnerCommission]:
        payments = await partner_dal.list_refunded_payments_with_active_commission(
            session,
            limit=limit,
        )
        reversed_decisions: list[PartnerCommission] = []
        for payment in payments:
            decision = await self.reverse_payment(session, int(payment.payment_id))
            if decision:
                reversed_decisions.append(decision)
        return reversed_decisions

    async def change_commission_rate(
        self,
        session: AsyncSession,
        *,
        partner_id: int,
        commission_bps: int,
        actor_admin_id: int,
        reason: str,
    ) -> PartnerProfile:
        if commission_bps < 0 or commission_bps > 10000:
            raise PartnerError("invalid_commission_rate", 400)
        if not reason.strip():
            raise PartnerError("reason_required", 400)
        profile = await partner_dal.get_profile_by_id(session, partner_id, for_update=True)
        if not profile:
            raise PartnerError("partner_not_found", 404)
        old = int(profile.commission_bps)
        profile.commission_bps = commission_bps
        profile.updated_at = datetime.now(UTC)
        await session.flush()
        await partner_dal.create_audit_event(
            session,
            event_type="commission_rate_changed",
            actor_type="admin",
            partner_id=partner_id,
            actor_user_id=actor_admin_id,
            old_values_json=compact_json({"commission_bps": old}),
            new_values_json=compact_json({"commission_bps": commission_bps}),
            reason=reason.strip(),
        )
        return profile

    async def adjust_balance(
        self,
        session: AsyncSession,
        *,
        partner_id: int,
        currency: str,
        scale: int,
        mode: str,
        amount_minor: int,
        reason: str,
        actor_admin_id: int,
        idempotency_key: str,
        allow_negative: bool = False,
        internal_reference: str | None = None,
    ) -> tuple[PartnerLedgerEntry, int]:
        if not reason.strip():
            raise PartnerError("reason_required", 400)
        profile = await partner_dal.get_profile_by_id(session, partner_id, for_update=True)
        if not profile:
            raise PartnerError("partner_not_found", 404)
        existing = await partner_dal.get_ledger_entry_by_key(session, idempotency_key)
        if existing:
            balance = await partner_dal.balance_minor(session, partner_id, currency)
            return existing, balance
        current = await partner_dal.balance_minor(session, partner_id, currency)
        normalized_mode = mode.strip().lower()
        if normalized_mode == "add":
            delta = abs(amount_minor)
        elif normalized_mode == "subtract":
            delta = -abs(amount_minor)
        elif normalized_mode == "set":
            delta = amount_minor - current
        else:
            raise PartnerError("invalid_adjustment_mode", 400)
        result = current + delta
        if result < 0 and not allow_negative:
            raise PartnerError("insufficient_partner_balance", 409)
        entry = await partner_dal.create_ledger_entry(
            session,
            partner_id=partner_id,
            currency=currency.upper(),
            currency_scale=scale,
            amount_minor=delta,
            kind="manual_adjustment",
            state="posted",
            reference_type="admin_adjustment",
            reference_id=internal_reference or idempotency_key,
            idempotency_key=idempotency_key,
            actor_admin_id=actor_admin_id,
            reason=reason.strip(),
            metadata_json=compact_json(
                {"mode": normalized_mode, "before_minor": current, "after_minor": result}
            ),
            posted_at=datetime.now(UTC),
        )
        await partner_dal.create_audit_event(
            session,
            event_type="balance_adjusted",
            actor_type="admin",
            partner_id=partner_id,
            actor_user_id=actor_admin_id,
            old_values_json=compact_json({"currency": currency.upper(), "balance_minor": current}),
            new_values_json=compact_json({"currency": currency.upper(), "balance_minor": result}),
            reason=reason.strip(),
        )
        await events.emit_model(
            PartnerBalanceAdjustedPayload(
                partner_id=partner_id,
                currency=currency.upper(),
                amount_minor=delta,
                balance_minor=result,
                adjusted_at=datetime.now(UTC),
            )
        )
        return entry, result

    async def reserve_subscription_spend(
        self,
        session: AsyncSession,
        *,
        profile: PartnerProfile,
        payment_id: int,
        currency: str,
        scale: int,
        amount_minor: int,
    ) -> PartnerLedgerEntry:
        key = f"subscription-spend:{payment_id}"
        existing = await partner_dal.get_ledger_entry_by_key(session, key)
        if existing:
            return existing
        current = await partner_dal.balance_minor(session, int(profile.partner_id), currency)
        if current < amount_minor:
            raise PartnerError("insufficient_partner_balance", 409)
        entry = await partner_dal.create_ledger_entry(
            session,
            partner_id=int(profile.partner_id),
            currency=currency.upper(),
            currency_scale=scale,
            amount_minor=-amount_minor,
            kind="subscription_spend",
            state="posted",
            reference_type="payment",
            reference_id=str(payment_id),
            idempotency_key=key,
            posted_at=datetime.now(UTC),
        )
        await events.emit_model(
            PartnerBalanceSpentPayload(
                partner_id=int(profile.partner_id),
                payment_db_id=payment_id,
                currency=currency.upper(),
                amount_minor=amount_minor,
                spent_at=datetime.now(UTC),
            )
        )
        return entry

    async def release_subscription_spend(
        self,
        session: AsyncSession,
        *,
        payment_id: int,
    ) -> PartnerLedgerEntry | None:
        spend = await partner_dal.get_ledger_entry_by_key(
            session, f"subscription-spend:{payment_id}"
        )
        if not spend:
            return None
        await partner_dal.get_profile_by_id(
            session,
            int(spend.partner_id),
            for_update=True,
        )
        key = f"subscription-spend-release:{payment_id}"
        existing = await partner_dal.get_ledger_entry_by_key(session, key)
        if existing:
            return existing
        return await partner_dal.create_ledger_entry(
            session,
            partner_id=int(spend.partner_id),
            currency=str(spend.currency),
            currency_scale=int(spend.currency_scale),
            amount_minor=-int(spend.amount_minor),
            kind="subscription_spend_release",
            state="posted",
            reference_type="payment",
            reference_id=str(payment_id),
            idempotency_key=key,
            reason="subscription activation failed",
            posted_at=datetime.now(UTC),
        )
