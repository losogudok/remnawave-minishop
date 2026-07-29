from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .config import WataTerminalProfile, _parse_wata_datetime


class WataLinkExpirationContext(Protocol):
    def profile_for_payment(self, payment: Any) -> WataTerminalProfile: ...

    def profile_enabled(self, provider: str) -> bool: ...

    async def get_payment_link(
        self,
        payment_link_id: str,
        *,
        profile: WataTerminalProfile | None = None,
    ) -> tuple[bool, Any]: ...

    def payment_link_expired_locally(
        self,
        payment: Any,
        *,
        grace_seconds: int = 0,
    ) -> bool: ...


def payment_link_expired_locally(
    payment: Any,
    *,
    profile: WataTerminalProfile,
    grace_seconds: int = 0,
) -> bool:
    """Return whether the lifetime requested when creating the link elapsed."""

    created_at = getattr(payment, "created_at", None)
    created_dt: datetime | None
    if isinstance(created_at, datetime):
        created_dt = (
            created_at.replace(tzinfo=UTC)
            if created_at.tzinfo is None
            else created_at.astimezone(UTC)
        )
    else:
        created_dt = _parse_wata_datetime(created_at)
    if created_dt is None:
        return False
    expires_at = created_dt + timedelta(
        minutes=profile.link_ttl_minutes,
        seconds=max(0, int(grace_seconds)),
    )
    return expires_at <= datetime.now(UTC)


async def expired_link_payload_for_payment(
    service: WataLinkExpirationContext,
    payment: Any,
) -> Mapping[str, Any] | None:
    """Resolve an expired Wata link, falling back to its requested local TTL."""

    if (
        str(getattr(payment, "status", "") or "").strip().lower()
        == "succeeded_pending_finalization"
    ):
        return None
    profile = service.profile_for_payment(payment)
    if not service.profile_enabled(profile.provider):
        return None
    provider_payment_id = str(getattr(payment, "provider_payment_id", "") or "").strip()
    if not provider_payment_id:
        return None

    success, data = await service.get_payment_link(provider_payment_id, profile=profile)
    if not success or not isinstance(data, dict):
        return (
            {"id": provider_payment_id} if service.payment_link_expired_locally(payment) else None
        )

    expiration_raw = data.get("expirationDateTime") or data.get("expiration_date_time")
    expiration_dt = _parse_wata_datetime(expiration_raw)
    if expiration_dt is None:
        return (
            {"id": provider_payment_id} if service.payment_link_expired_locally(payment) else None
        )
    if expiration_dt > datetime.now(UTC):
        return None
    return data
