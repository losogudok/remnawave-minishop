"""Authenticated Web Push device routes for the Mini App."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import web
from pydantic import Field, field_validator
from sqlalchemy import select

from bot.app.web.context import get_session_factory, get_settings, get_subscription_service
from bot.app.web.http_contracts import HttpBodyModel, HttpResponseModel
from bot.app.web.request_parsing import parse_body_or_400
from bot.app.web.route_contracts import (
    BOOLEAN_SCHEMA,
    STRING_SCHEMA,
    RouteContract,
    ok_envelope_with,
    schema_ref,
)
from bot.app.web.webapp.common import _json_error, _require_user_id
from bot.app.web.webapp.contract_schemas import user_contract
from bot.app.web.webapp.rate_limits import check_request_limits, client_ip
from bot.app.web.webapp.response_helpers import json_response
from bot.services.user_notification_policy import push_notifications_allowed
from db.dal import subscription_dal, user_dal
from db.setup_reward_models import SetupReward

from .crypto import WebPushKeyError, decode_subscription_keys
from .provider import web_push_configured
from .sender import PushEndpointError, validate_endpoint
from .store import WebPushStore, WebPushSubscriptionMissing, endpoint_hash

logger = logging.getLogger(__name__)

MAX_SUBSCRIPTION_BODY_BYTES = 16 * 1024
MAX_ENDPOINT_LENGTH = 4096
_MAX_LABEL_LENGTH = 120


class PushSubscriptionKeysBody(HttpBodyModel):
    p256dh: str = Field(max_length=512)
    auth: str = Field(max_length=128)


class PushSubscriptionBody(HttpBodyModel):
    endpoint: str = Field(max_length=MAX_ENDPOINT_LENGTH)
    expiration_time: float | None = Field(default=None, alias="expirationTime")
    keys: PushSubscriptionKeysBody


class PushSubscribeBody(HttpBodyModel):
    subscription: PushSubscriptionBody
    label: str = ""

    @field_validator("label")
    @classmethod
    def _bound_label(cls, value: str) -> str:
        return str(value or "").strip()[:_MAX_LABEL_LENGTH]


class PushStatusBody(HttpBodyModel):
    endpoint: str | None = Field(default=None, max_length=MAX_ENDPOINT_LENGTH)


class PushUnsubscribeBody(HttpBodyModel):
    endpoint: str | None = Field(default=None, max_length=MAX_ENDPOINT_LENGTH)
    device_id: str | None = Field(default=None, max_length=64)


class PushDeviceOut(HttpResponseModel):
    id: str
    label: str
    current: bool
    created_at: datetime | None = None
    last_seen_at: datetime | None = None


class PushStatusOut(HttpResponseModel):
    configured: bool
    pwa_enabled: bool
    public_key: str
    preference_enabled: bool
    devices: list[PushDeviceOut]
    current_device_id: str = ""


class SetupRewardClaimBody(HttpBodyModel):
    reward_type: str = Field(alias="rewardType", pattern="^(install|push)$")
    endpoint: str | None = Field(default=None, max_length=MAX_ENDPOINT_LENGTH)


class SetupRewardInstallEvidenceBody(HttpBodyModel):
    standalone: bool


class SetupRewardOut(HttpResponseModel):
    reward_type: str
    completed: bool
    state: str
    days: int = 0
    end_date: datetime | None = None


class SetupRewardsStatusOut(HttpResponseModel):
    install_days: int
    push_days: int
    push_available: bool
    install_completed: bool
    push_completed: bool
    install_state: str
    push_state: str
    install_reward_days: int
    push_reward_days: int
    install_end_date: datetime | None = None
    push_end_date: datetime | None = None


def _pwa_enabled(settings: Any) -> bool:
    return bool(settings.WEBAPP_ENABLED and settings.PWA_ENABLED)


def _request_agent(request: web.Request) -> str:
    return str(request.headers.get("User-Agent") or "")[:255]


async def _load_user(request: web.Request, user_id: int) -> Any | None:
    async with get_session_factory(request)() as session:
        return await user_dal.get_user_by_id(session, user_id)


async def _status_payload(
    request: web.Request,
    *,
    user_id: int,
    user: Any,
    endpoint: str = "",
) -> PushStatusOut:
    settings = get_settings(request)
    configured = web_push_configured(settings)
    store = WebPushStore(settings, get_session_factory(request))
    rows = await store.list_subscriptions(user_id)
    current_hash = endpoint_hash(endpoint) if endpoint else ""
    current_id = next((row.id for row in rows if row.endpoint_hash == current_hash), "")
    return PushStatusOut(
        configured=configured,
        pwa_enabled=_pwa_enabled(settings),
        public_key=str(settings.WEB_PUSH_VAPID_PUBLIC_KEY or "") if configured else "",
        preference_enabled=push_notifications_allowed(user),
        devices=[
            PushDeviceOut(
                id=row.id,
                label=row.label,
                current=bool(current_id and row.id == current_id),
                created_at=row.created_at,
                last_seen_at=row.last_seen_at,
            )
            for row in rows
        ],
        current_device_id=current_id,
    )


async def _bounded_body(request: web.Request) -> None:
    length = request.content_length
    if length is not None and length > MAX_SUBSCRIPTION_BODY_BYTES:
        raise web.HTTPRequestEntityTooLarge(
            max_size=MAX_SUBSCRIPTION_BODY_BYTES,
            actual_size=length,
            text='{"ok": false, "error": "payload_too_large"}',
            content_type="application/json",
        )


async def _rate_limited(request: web.Request, user_id: int, action: str) -> web.Response | None:
    return await check_request_limits(
        request,
        (
            (f"web-push:{action}:user:{int(user_id)}", 30),
            (f"web-push:{action}:ip:{client_ip(request)}", 120),
        ),
        window_seconds=60,
    )


async def _report_pending_broadcast_callbacks(
    request: web.Request,
    settings: Any,
    *,
    has_candidates: bool = True,
) -> None:
    if not has_candidates:
        return
    from .callbacks import report_pending_broadcast_callbacks

    try:
        await report_pending_broadcast_callbacks(settings, get_session_factory(request))
    except Exception:
        logger.exception("Failed to flush Web Push broadcast callback outbox.")


async def web_push_status_route(request: web.Request) -> web.Response:
    user_id = _require_user_id(request)
    limited = await _rate_limited(request, user_id, "status")
    if limited is not None:
        return limited
    await _bounded_body(request)
    body = await parse_body_or_400(
        request.clone(client_max_size=MAX_SUBSCRIPTION_BODY_BYTES), PushStatusBody
    )
    user = await _load_user(request, user_id)
    if user is None or user.is_banned:
        return _json_error(403, "access_denied", "Access denied")
    status = await _status_payload(
        request,
        user_id=user_id,
        user=user,
        endpoint=str(body.endpoint or ""),
    )
    return json_response({"ok": True, **status.model_dump(mode="json")})


async def web_push_subscribe_route(request: web.Request) -> web.Response:
    return await _store_subscription(request, reactivate=True)


async def web_push_sync_route(request: web.Request) -> web.Response:
    return await _store_subscription(request, reactivate=False)


async def _store_subscription(request: web.Request, *, reactivate: bool) -> web.Response:
    user_id = _require_user_id(request)
    limited = await _rate_limited(request, user_id, "subscribe")
    if limited is not None:
        return limited
    await _bounded_body(request)
    settings = get_settings(request)
    if not web_push_configured(settings):
        return _json_error(503, "web_push_not_configured", "Web Push is not available")
    body = await parse_body_or_400(
        request.clone(client_max_size=MAX_SUBSCRIPTION_BODY_BYTES), PushSubscribeBody
    )
    subscription = body.subscription
    if len(subscription.endpoint) > MAX_ENDPOINT_LENGTH:
        return _json_error(400, "invalid_endpoint", "Invalid subscription endpoint")
    try:
        endpoint = validate_endpoint(subscription.endpoint)
    except PushEndpointError:
        return _json_error(400, "invalid_endpoint", "Invalid subscription endpoint")
    try:
        decode_subscription_keys(
            {
                "p256dh": subscription.keys.p256dh,
                "auth": subscription.keys.auth,
            }
        )
    except WebPushKeyError:
        return _json_error(400, "invalid_subscription_keys", "Invalid subscription keys")

    user = await _load_user(request, user_id)
    if user is None or user.is_banned:
        return _json_error(403, "access_denied", "Access denied")
    expiration_time: int | None = None
    if subscription.expiration_time is not None:
        value = float(subscription.expiration_time)
        if not math.isfinite(value) or value < 0 or value > 10**15:
            return _json_error(400, "invalid_expiration", "Invalid subscription expiration")
        expiration_time = int(value)
    store = WebPushStore(settings, get_session_factory(request))
    try:
        record, _outcome, skipped_broadcasts = await store.upsert_subscription(
            user_id=user_id,
            endpoint=endpoint,
            subscription_keys={
                "p256dh": subscription.keys.p256dh,
                "auth": subscription.keys.auth,
            },
            label=body.label,
            user_agent=_request_agent(request),
            expiration_time=expiration_time,
            reactivate=reactivate,
        )
    except WebPushSubscriptionMissing:
        status = await _status_payload(request, user_id=user_id, user=user, endpoint=endpoint)
        return json_response({"ok": True, "device_id": "", **status.model_dump(mode="json")})
    except Exception:
        logger.exception("Failed to store a Web Push subscription for user %s.", user_id)
        return _json_error(500, "web_push_storage_error", "Could not store subscription")
    await _report_pending_broadcast_callbacks(
        request,
        settings,
        has_candidates=bool(skipped_broadcasts),
    )
    status = await _status_payload(request, user_id=user_id, user=user, endpoint=endpoint)
    return json_response({"ok": True, "device_id": record.id, **status.model_dump(mode="json")})


async def web_push_unsubscribe_route(request: web.Request) -> web.Response:
    user_id = _require_user_id(request)
    limited = await _rate_limited(request, user_id, "unsubscribe")
    if limited is not None:
        return limited
    await _bounded_body(request)
    body = await parse_body_or_400(
        request.clone(client_max_size=MAX_SUBSCRIPTION_BODY_BYTES), PushUnsubscribeBody
    )
    endpoint = str(body.endpoint or "").strip()
    device_id = str(body.device_id or "").strip()
    if not endpoint and not device_id:
        return _json_error(
            400,
            "subscription_required",
            "Subscription endpoint or device id is required",
        )
    if endpoint:
        if len(endpoint) > MAX_ENDPOINT_LENGTH:
            return _json_error(400, "invalid_endpoint", "Invalid subscription endpoint")
        try:
            endpoint = validate_endpoint(endpoint)
        except PushEndpointError:
            return _json_error(400, "invalid_endpoint", "Invalid subscription endpoint")
    user = await _load_user(request, user_id)
    if user is None or user.is_banned:
        return _json_error(403, "access_denied", "Access denied")
    settings = get_settings(request)
    store = WebPushStore(settings, get_session_factory(request))
    revoked = await store.revoke_subscription(
        user_id=user_id,
        endpoint=endpoint,
        device_id=device_id,
    )
    await _report_pending_broadcast_callbacks(
        request,
        settings,
        has_candidates=bool(revoked.broadcast_delivery_ids),
    )
    status = await _status_payload(request, user_id=user_id, user=user, endpoint=endpoint)
    return json_response({"ok": True, "revoked": revoked.found, **status.model_dump(mode="json")})


async def setup_rewards_status_route(request: web.Request) -> web.Response:
    user_id = _require_user_id(request)
    user = await _load_user(request, user_id)
    if user is None or user.is_banned:
        return _json_error(403, "access_denied", "Access denied")
    settings = get_settings(request)
    async with get_session_factory(request)() as session:
        rewards = list(
            (await session.scalars(select(SetupReward).where(SetupReward.user_id == user_id))).all()
        )
    by_type = {reward.reward_type: reward for reward in rewards}
    install = by_type.get("install")
    push = by_type.get("push")

    def completed(reward: SetupReward | None) -> bool:
        return reward is not None

    def state(reward: SetupReward | None) -> str:
        return reward.state if reward is not None else "not_started"

    def accepted_days(reward: SetupReward | None) -> int:
        return int(reward.accepted_days or 0) if reward is not None else 0

    def confirmed_end(reward: SetupReward | None) -> datetime | None:
        return reward.confirmed_end_date if reward is not None else None

    install_confirmed_end = confirmed_end(install)
    push_confirmed_end = confirmed_end(push)
    return json_response(
        {
            "ok": True,
            "install_days": int(settings.SETUP_INSTALL_REWARD_DAYS),
            "push_days": int(settings.SETUP_PUSH_REWARD_DAYS),
            "push_available": bool(_pwa_enabled(settings) and web_push_configured(settings)),
            "install_completed": completed(install),
            "push_completed": completed(push),
            "install_state": state(install),
            "push_state": state(push),
            "install_reward_days": accepted_days(install),
            "push_reward_days": accepted_days(push),
            "install_end_date": install_confirmed_end.isoformat()
            if install_confirmed_end
            else None,
            "push_end_date": push_confirmed_end.isoformat() if push_confirmed_end else None,
        }
    )


async def setup_reward_install_evidence_route(request: web.Request) -> web.Response:
    user_id = _require_user_id(request)
    limited = await _rate_limited(request, user_id, "setup-reward-evidence")
    if limited is not None:
        return limited
    body = await parse_body_or_400(request, SetupRewardInstallEvidenceBody)
    if not body.standalone:
        return _json_error(400, "standalone_required", "Open the installed app first")
    user = await _load_user(request, user_id)
    if user is None or user.is_banned:
        return _json_error(403, "access_denied", "Access denied")
    async with get_session_factory(request)() as session:
        await user_dal.lock_user_entitlement(session, user_id)
        reward = await _get_reward(session, user_id, "install")
        if reward is None:
            reward = SetupReward(
                id=uuid.uuid4().hex,
                user_id=user_id,
                reward_type="install",
                state="completed",
            )
            session.add(reward)
            await session.commit()
    return json_response({"ok": True, "completed": True})


async def setup_reward_claim_route(request: web.Request) -> web.Response:
    user_id = _require_user_id(request)
    limited = await _rate_limited(request, user_id, "setup-reward")
    if limited is not None:
        return limited
    body = await parse_body_or_400(request, SetupRewardClaimBody)
    reward_type = body.reward_type
    settings = get_settings(request)
    user = await _load_user(request, user_id)
    if user is None or user.is_banned:
        return _json_error(403, "access_denied", "Access denied")

    # Completion is durable independently of the current reward amount. A zero
    # setting never consumes the one-time claim.
    if reward_type == "install":
        async with get_session_factory(request)() as session:
            await user_dal.lock_user_entitlement(session, user_id)
            reward = await _get_reward(session, user_id, "install")
            if reward is None:
                return _json_error(409, "setup_not_completed", "Open the installed app first")
    else:
        async with get_session_factory(request)() as session:
            await user_dal.lock_user_entitlement(session, user_id)
            reward = await _get_reward(session, user_id, "push")
            if reward is None:
                endpoint = str(body.endpoint or "").strip()
                if not endpoint:
                    return _json_error(
                        409, "push_device_not_confirmed", "Enable notifications on this device"
                    )
                try:
                    endpoint = validate_endpoint(endpoint)
                except PushEndpointError:
                    return _json_error(400, "invalid_endpoint", "Invalid subscription endpoint")
                if not push_notifications_allowed(user):
                    return _json_error(
                        409, "push_preference_disabled", "Enable notification preferences"
                    )
                from db.web_push_models import WebPushSubscription

                from .store import endpoint_hash

                device = await session.scalar(
                    select(WebPushSubscription).where(
                        WebPushSubscription.user_id == user_id,
                        WebPushSubscription.endpoint_hash == endpoint_hash(endpoint),
                        WebPushSubscription.revoked_at.is_(None),
                    )
                )
                if device is None:
                    return _json_error(
                        409, "push_device_not_confirmed", "Enable notifications on this device"
                    )
                reward = SetupReward(
                    id=uuid.uuid4().hex,
                    user_id=user_id,
                    reward_type="push",
                    state="completed",
                )
                session.add(reward)
                await session.commit()

    configured_days = int(
        settings.SETUP_INSTALL_REWARD_DAYS
        if reward_type == "install"
        else settings.SETUP_PUSH_REWARD_DAYS
    )
    async with get_session_factory(request)() as session:
        await user_dal.lock_user_entitlement(session, user_id)
        reward = await _get_reward(session, user_id, reward_type)
        if reward is None:
            return _json_error(409, "setup_not_completed", "Complete setup first")
        if reward.accepted_days is not None:
            if reward.state == "confirmed":
                return json_response(_reward_payload(reward))
        elif configured_days <= 0:
            return json_response(_reward_payload(reward))
        else:
            active_sub = await subscription_dal.get_active_subscription_by_user_id(session, user_id)
            if active_sub is None or not active_sub.end_date:
                return _json_error(
                    409, "active_subscription_required", "An active subscription is required"
                )
            now = datetime.now(UTC)
            current_end = active_sub.end_date
            if current_end.tzinfo is None:
                current_end = current_end.replace(tzinfo=UTC)
            pending_targets = list(
                (
                    await session.scalars(
                        select(SetupReward.target_end_date).where(
                            SetupReward.user_id == user_id,
                            SetupReward.accepted_days.is_not(None),
                            SetupReward.target_end_date.is_not(None),
                            SetupReward.state == "pending",
                        )
                    )
                ).all()
            )
            normalized_pending_targets = [
                pending_target.replace(tzinfo=UTC)
                if pending_target.tzinfo is None
                else pending_target
                for pending_target in pending_targets
                if pending_target is not None
            ]
            target = max(current_end, now, *normalized_pending_targets) + timedelta(
                days=configured_days
            )
            reward.accepted_days = configured_days
            reward.subscription_id = int(active_sub.subscription_id)
            reward.target_end_date = target
            reward.state = "pending"
            # Reserve the accepted days on the entitlement row while holding
            # the same per-user lock used by renewals. A renewal that commits
            # after this claim must extend from this reserved expiration.
            await subscription_dal.update_subscription_end_date(
                session, int(active_sub.subscription_id), target
            )
            await session.commit()

    # The grant uses an absolute target and is safe to replay after an
    # ambiguous panel response. It is only allowed to touch the same active
    # subscription that was eligible when the claim was accepted.
    async with get_session_factory(request)() as session:
        reward = await _get_reward(session, user_id, reward_type)
        active_sub = await subscription_dal.get_active_subscription_by_user_id(session, user_id)
        if (
            reward is None
            or reward.accepted_days is None
            or reward.target_end_date is None
            or active_sub is None
            or int(active_sub.subscription_id) != reward.subscription_id
        ):
            return _json_error(
                409, "active_subscription_required", "An active subscription is required"
            )
        if reward.state == "confirmed":
            return json_response(_reward_payload(reward))
        target_end_date = reward.target_end_date

    async with get_session_factory(request)() as session:
        confirmed_end = await get_subscription_service(
            request
        ).extend_subscription_reward_expiration(
            session,
            user_id,
            int(reward.subscription_id or 0),
            target_end_date,
        )
        reward = await _get_reward(session, user_id, reward_type)
        if confirmed_end is not None and reward is not None:
            reward.state = "confirmed"
            reward.confirmed_end_date = confirmed_end
        # A target-date extension remains a durable replay reservation when
        # the panel response is ambiguous; committing it avoids adding days a
        # second time on retry.
        await session.commit()
        if reward is None:
            return _json_error(500, "setup_reward_state_missing", "Could not confirm reward")
        return json_response(_reward_payload(reward))


async def _get_reward(session: Any, user_id: int, reward_type: str) -> SetupReward | None:
    return await session.scalar(
        select(SetupReward).where(
            SetupReward.user_id == user_id,
            SetupReward.reward_type == reward_type,
        )
    )


def _reward_payload(reward: SetupReward) -> dict[str, object]:
    return {
        "ok": True,
        "reward_type": reward.reward_type,
        "completed": True,
        "state": reward.state,
        "days": int(reward.accepted_days or 0),
        "end_date": reward.confirmed_end_date.isoformat() if reward.confirmed_end_date else None,
    }


_PUSH_DEVICE_SCHEMA = schema_ref(PushDeviceOut)

WEB_PUSH_ROUTE_CONTRACTS: dict[str, RouteContract] = {
    "web_push_status_route": user_contract(
        request_model=PushStatusBody,
        response_schema=ok_envelope_with(
            {
                "configured": BOOLEAN_SCHEMA,
                "pwa_enabled": BOOLEAN_SCHEMA,
                "public_key": STRING_SCHEMA,
                "preference_enabled": BOOLEAN_SCHEMA,
                "devices": {"type": "array", "items": _PUSH_DEVICE_SCHEMA},
                "current_device_id": STRING_SCHEMA,
            }
        ),
        models=(PushDeviceOut,),
    ),
    "web_push_subscribe_route": user_contract(
        request_model=PushSubscribeBody,
        response_schema=ok_envelope_with(
            {
                "device_id": STRING_SCHEMA,
                "configured": BOOLEAN_SCHEMA,
                "pwa_enabled": BOOLEAN_SCHEMA,
                "public_key": STRING_SCHEMA,
                "preference_enabled": BOOLEAN_SCHEMA,
                "devices": {"type": "array", "items": _PUSH_DEVICE_SCHEMA},
                "current_device_id": STRING_SCHEMA,
            }
        ),
        models=(PushDeviceOut,),
    ),
    "web_push_unsubscribe_route": user_contract(
        request_model=PushUnsubscribeBody,
        response_schema=ok_envelope_with(
            {
                "revoked": BOOLEAN_SCHEMA,
                "configured": BOOLEAN_SCHEMA,
                "pwa_enabled": BOOLEAN_SCHEMA,
                "public_key": STRING_SCHEMA,
                "preference_enabled": BOOLEAN_SCHEMA,
                "devices": {"type": "array", "items": _PUSH_DEVICE_SCHEMA},
                "current_device_id": STRING_SCHEMA,
            }
        ),
        models=(PushDeviceOut,),
    ),
}

WEB_PUSH_ROUTE_CONTRACTS["web_push_sync_route"] = WEB_PUSH_ROUTE_CONTRACTS[
    "web_push_subscribe_route"
]
WEB_PUSH_ROUTE_CONTRACTS.update(
    {
        "setup_rewards_status_route": user_contract(
            response_schema=ok_envelope_with(
                {
                    "install_days": {"type": "integer"},
                    "push_days": {"type": "integer"},
                    "push_available": BOOLEAN_SCHEMA,
                    "install_completed": BOOLEAN_SCHEMA,
                    "push_completed": BOOLEAN_SCHEMA,
                    "install_state": STRING_SCHEMA,
                    "push_state": STRING_SCHEMA,
                    "install_reward_days": {"type": "integer"},
                    "push_reward_days": {"type": "integer"},
                    "install_end_date": {"type": ["string", "null"], "format": "date-time"},
                    "push_end_date": {"type": ["string", "null"], "format": "date-time"},
                }
            ),
            models=(SetupRewardsStatusOut,),
        ),
        "setup_reward_install_evidence_route": user_contract(
            request_model=SetupRewardInstallEvidenceBody,
            response_schema=ok_envelope_with({"completed": BOOLEAN_SCHEMA}),
        ),
        "setup_reward_claim_route": user_contract(
            request_model=SetupRewardClaimBody,
            response_schema=ok_envelope_with(
                {
                    "reward_type": STRING_SCHEMA,
                    "completed": BOOLEAN_SCHEMA,
                    "state": STRING_SCHEMA,
                    "days": {"type": "integer"},
                    "end_date": {"type": ["string", "null"], "format": "date-time"},
                }
            ),
            models=(SetupRewardOut,),
        ),
    }
)


def register_web_push_contracts() -> None:
    from bot.app.web.route_contracts import register_contract

    for handler_name, contract in WEB_PUSH_ROUTE_CONTRACTS.items():
        register_contract(handler_name, contract)


__all__ = [
    "WEB_PUSH_ROUTE_CONTRACTS",
    "PushSubscribeBody",
    "PushUnsubscribeBody",
    "register_web_push_contracts",
    "setup_reward_claim_route",
    "setup_reward_install_evidence_route",
    "setup_rewards_status_route",
    "web_push_status_route",
    "web_push_subscribe_route",
    "web_push_unsubscribe_route",
]
