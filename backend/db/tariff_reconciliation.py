"""Safe, idempotent reconciliation of active subscription tariff bindings."""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.tariffs_config import Tariff, TariffsConfig
from db.models import Payment, Subscription

logger = logging.getLogger(__name__)

MAX_RECONCILIATION_ITEMS = 200


@dataclass(frozen=True)
class TariffReconciliationItem:
    subscription_id: int
    user_id: int
    current_tariff_key: str | None
    proposed_tariff_key: str | None
    source: str | None
    status: str
    reason: str
    applied: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "user_id": self.user_id,
            "current_tariff_key": self.current_tariff_key,
            "proposed_tariff_key": self.proposed_tariff_key,
            "source": self.source,
            "status": self.status,
            "reason": self.reason,
            "applied": self.applied,
        }


@dataclass
class TariffReconciliationReport:
    dry_run: bool
    scanned: int = 0
    healthy: int = 0
    candidates: int = 0
    applied: int = 0
    unresolved: int = 0
    items: list[TariffReconciliationItem] = field(default_factory=list)
    affected_user_ids: set[int] = field(default_factory=set)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    items_truncated: bool = False

    def add_item(self, item: TariffReconciliationItem) -> None:
        if len(self.items) < MAX_RECONCILIATION_ITEMS:
            self.items.append(item)
        else:
            self.items_truncated = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "scanned": self.scanned,
            "healthy": self.healthy,
            "candidates": self.candidates,
            "applied": self.applied,
            "unresolved": self.unresolved,
            "generated_at": self.generated_at.isoformat(),
            "items_truncated": self.items_truncated,
            "items": [item.to_payload() for item in self.items],
        }


@dataclass(frozen=True)
class _PaymentTariffEvidence:
    tariff: Tariff
    payment_id: int


def _period_tariff(config: TariffsConfig, key: str | None) -> Tariff | None:
    if not key:
        return None
    tariff = config.get(str(key))
    if tariff is None or not tariff.enabled or tariff.billing_model != "period":
        return None
    return tariff


async def _latest_payment_tariffs(
    session: AsyncSession,
    config: TariffsConfig,
) -> dict[int, _PaymentTariffEvidence]:
    normalized_sale_mode = func.lower(func.coalesce(Payment.sale_mode, ""))
    statement = (
        select(
            Payment.user_id,
            Payment.tariff_key,
            Payment.payment_id,
        )
        .where(
            func.lower(Payment.status) == "succeeded",
            Payment.tariff_key.is_not(None),
            or_(
                normalized_sale_mode == "subscription",
                normalized_sale_mode.like("subscription@%"),
                normalized_sale_mode == "tariff_upgrade",
                normalized_sale_mode.like("tariff_upgrade@%"),
            ),
        )
        .order_by(
            Payment.user_id.asc(),
            Payment.created_at.desc().nullslast(),
            Payment.payment_id.desc(),
        )
    )
    evidence: dict[int, _PaymentTariffEvidence] = {}
    seen_user_ids: set[int] = set()
    for user_id, tariff_key, payment_id in (await session.execute(statement)).all():
        normalized_user_id = int(user_id)
        if normalized_user_id in seen_user_ids:
            continue
        seen_user_ids.add(normalized_user_id)
        tariff = _period_tariff(config, str(tariff_key or "").strip())
        if tariff is not None:
            evidence[normalized_user_id] = _PaymentTariffEvidence(
                tariff=tariff,
                payment_id=int(payment_id),
            )
    return evidence


def _binding_candidate(
    *,
    subscription: Subscription,
    config: TariffsConfig,
    payment_evidence: _PaymentTariffEvidence | None,
    single_period_tariff: Tariff | None,
    active_subscription_count: int,
) -> tuple[Tariff | None, str | None, str]:
    current_key = str(subscription.tariff_key or "").strip()
    current_tariff = config.get(current_key) if current_key else None
    if current_tariff is not None:
        if current_tariff.key == current_key:
            return None, None, "already_canonical"
        return current_tariff, "legacy_key", "canonicalize_legacy_key"

    if active_subscription_count > 1:
        return None, None, "multiple_active_subscriptions"
    if payment_evidence is not None:
        return (
            payment_evidence.tariff,
            "payment_history",
            f"succeeded_payment:{payment_evidence.payment_id}",
        )
    if not current_key and single_period_tariff is not None:
        return (
            single_period_tariff,
            "single_tariff_reconciliation",
            "single_enabled_period_tariff",
        )
    if current_key:
        return None, None, "tariff_key_not_in_catalog"
    return None, None, "ambiguous_missing_tariff"


def _apply_binding(
    subscription: Subscription,
    *,
    tariff: Tariff,
    source: str,
    note: str,
    now: datetime,
) -> None:
    subscription.tariff_key = tariff.key
    subscription.tariff_binding_source = source
    subscription.tariff_bound_at = now
    subscription.tariff_binding_note = note[:255]
    if subscription.tier_baseline_bytes is None:
        subscription.tier_baseline_bytes = (
            int(subscription.traffic_limit_bytes)
            if subscription.traffic_limit_bytes is not None
            else tariff.monthly_bytes
        )
    if subscription.topup_balance_bytes is None:
        subscription.topup_balance_bytes = 0
    if subscription.premium_baseline_bytes is None:
        subscription.premium_baseline_bytes = tariff.premium_monthly_bytes
    if subscription.premium_topup_balance_bytes is None:
        subscription.premium_topup_balance_bytes = 0
    if subscription.premium_topup_used_bytes is None:
        subscription.premium_topup_used_bytes = 0
    if subscription.premium_used_bytes is None:
        subscription.premium_used_bytes = 0


async def reconcile_subscription_tariffs(
    session: AsyncSession,
    config: TariffsConfig,
    *,
    apply: bool = False,
) -> TariffReconciliationReport:
    """Inspect or safely repair active, non-trial tariff bindings.

    The function never guesses between several tariffs. It applies only a
    canonical legacy-key match, a successful subscription-payment match, or a
    single enabled period tariff for an otherwise unbound subscription.
    """

    now = datetime.now(UTC)
    statement = (
        select(Subscription)
        .where(
            Subscription.is_active.is_(True),
            Subscription.end_date > now,
            func.lower(func.coalesce(Subscription.provider, "")) != "trial",
            func.upper(func.coalesce(Subscription.status_from_panel, "")) != "TRIAL",
        )
        .order_by(Subscription.subscription_id.asc())
    )
    subscriptions = list((await session.execute(statement)).scalars().all())
    active_counts = Counter(int(subscription.user_id) for subscription in subscriptions)
    payment_evidence = await _latest_payment_tariffs(session, config)
    enabled_period_tariffs = [
        tariff for tariff in config.enabled_tariffs if tariff.billing_model == "period"
    ]
    single_period_tariff = enabled_period_tariffs[0] if len(enabled_period_tariffs) == 1 else None
    report = TariffReconciliationReport(dry_run=not apply, scanned=len(subscriptions))

    for subscription in subscriptions:
        user_id = int(subscription.user_id)
        target, source, reason = _binding_candidate(
            subscription=subscription,
            config=config,
            payment_evidence=payment_evidence.get(user_id),
            single_period_tariff=single_period_tariff,
            active_subscription_count=active_counts[user_id],
        )
        current_key = str(subscription.tariff_key or "").strip() or None
        if reason == "already_canonical":
            report.healthy += 1
            continue
        if target is None or source is None:
            report.unresolved += 1
            report.add_item(
                TariffReconciliationItem(
                    subscription_id=int(subscription.subscription_id),
                    user_id=user_id,
                    current_tariff_key=current_key,
                    proposed_tariff_key=None,
                    source=None,
                    status="unresolved",
                    reason=reason,
                )
            )
            continue

        report.candidates += 1
        item_applied = False
        if apply:
            _apply_binding(
                subscription,
                tariff=target,
                source=source,
                note=reason,
                now=now,
            )
            report.applied += 1
            report.affected_user_ids.add(user_id)
            item_applied = True
        report.add_item(
            TariffReconciliationItem(
                subscription_id=int(subscription.subscription_id),
                user_id=user_id,
                current_tariff_key=current_key,
                proposed_tariff_key=target.key,
                source=source,
                status="applied" if item_applied else "candidate",
                reason=reason,
                applied=item_applied,
            )
        )

    if apply and report.applied:
        await session.flush()
    log = logger.warning if report.unresolved else logger.info
    log(
        "Tariff reconciliation dry_run=%s scanned=%s healthy=%s candidates=%s "
        "applied=%s unresolved=%s",
        report.dry_run,
        report.scanned,
        report.healthy,
        report.candidates,
        report.applied,
        report.unresolved,
    )
    return report


__all__ = [
    "TariffReconciliationItem",
    "TariffReconciliationReport",
    "reconcile_subscription_tariffs",
]
