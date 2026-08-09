from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from db.dal import partner_dal, user_dal
from db.models import User

if TYPE_CHECKING:
    from bot.plugins import PluginContext
    from bot.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


def _event_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _event_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class PartnerEventReactionsMixin:
    if TYPE_CHECKING:
        ctx: PluginContext

        def _notification_service(self) -> NotificationService | None: ...

        async def _load_user(self, user_id: Any) -> User | None: ...

    async def _load_partner_user(self, partner_id: Any) -> User | None:
        if self.ctx.session_factory is None or partner_id is None:
            return None
        try:
            async with self.ctx.session_factory() as session:
                profile = await partner_dal.get_profile_by_id(session, int(partner_id))
                if profile is None or profile.user_id is None:
                    return None
                return await user_dal.get_user_by_id(session, int(profile.user_id))
        except Exception:
            logger.exception("Failed to load user for partner %s.", partner_id)
            return None

    async def _partner_user_from_payload(self, payload: dict[str, Any]) -> User | None:
        user_id = payload.get("user_id")
        if user_id is not None:
            return await self._load_user(user_id)
        return await self._load_partner_user(payload.get("partner_id"))

    async def on_partner_application_submitted(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        del event_name
        application_id = _event_int(payload.get("application_id"))
        user = await self._partner_user_from_payload(payload)
        service = self._notification_service()
        if application_id is None or user is None or service is None:
            return
        submitted_at = _event_datetime(payload.get("submitted_at")) or datetime.now(UTC)
        try:
            await service.notify_partner_application_submitted(
                application_id=application_id,
                user=user,
                submitted_at=submitted_at,
            )
        except Exception:
            logger.exception(
                "Failed to notify about partner application %s.",
                application_id,
            )

    async def on_partner_application_decided(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        del event_name
        application_id = _event_int(payload.get("application_id"))
        user = await self._partner_user_from_payload(payload)
        service = self._notification_service()
        if application_id is None or user is None or service is None:
            return
        decided_at = _event_datetime(payload.get("decided_at")) or datetime.now(UTC)
        try:
            await service.notify_partner_application_decided(
                application_id=application_id,
                user=user,
                status=str(payload.get("status") or ""),
                decided_at=decided_at,
            )
        except Exception:
            logger.exception(
                "Failed to notify about partner application decision %s.",
                application_id,
            )

    async def on_partner_status_changed(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        del event_name
        partner_id = _event_int(payload.get("partner_id"))
        user = await self._partner_user_from_payload(payload)
        service = self._notification_service()
        if partner_id is None or user is None or service is None:
            return
        changed_at = _event_datetime(payload.get("changed_at")) or datetime.now(UTC)
        try:
            await service.notify_partner_profile_status_changed(
                partner_id=partner_id,
                user=user,
                old_status=str(payload.get("old_status") or ""),
                status=str(payload.get("status") or ""),
                changed_at=changed_at,
            )
        except Exception:
            logger.exception("Failed to notify about partner %s status change.", partner_id)

    async def on_partner_withdrawal_requested(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        del event_name
        withdrawal_id = _event_int(payload.get("withdrawal_id"))
        user = await self._partner_user_from_payload(payload)
        service = self._notification_service()
        if withdrawal_id is None or user is None or service is None:
            return
        requested_at = _event_datetime(payload.get("requested_at")) or datetime.now(UTC)
        try:
            await service.notify_partner_withdrawal_requested(
                withdrawal_id=withdrawal_id,
                user=user,
                amount_minor=int(payload.get("amount_minor") or 0),
                currency=str(payload.get("currency") or ""),
                currency_scale=int(payload.get("currency_scale") or 0),
                requested_at=requested_at,
            )
        except Exception:
            logger.exception(
                "Failed to notify about partner withdrawal request %s.",
                withdrawal_id,
            )

    async def on_partner_withdrawal_status_changed(
        self,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        del event_name
        withdrawal_id = _event_int(payload.get("withdrawal_id"))
        user = await self._partner_user_from_payload(payload)
        service = self._notification_service()
        if withdrawal_id is None or user is None or service is None:
            return
        try:
            await service.notify_partner_withdrawal_status_changed(
                withdrawal_id=withdrawal_id,
                user=user,
                status=str(payload.get("status") or ""),
                amount_minor=int(payload.get("amount_minor") or 0),
                currency=str(payload.get("currency") or ""),
                currency_scale=int(payload.get("currency_scale") or 0),
            )
        except Exception:
            logger.exception(
                "Failed to notify about partner withdrawal status %s.",
                withdrawal_id,
            )
