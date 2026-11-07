"""Authenticated Web Push device routes for the Mini App."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

from aiohttp import web
from pydantic import Field, field_validator

from bot.app.web.context import get_session_factory, get_settings
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
from db.dal import user_dal

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


def register_web_push_contracts() -> None:
    from bot.app.web.route_contracts import register_contract

    for handler_name, contract in WEB_PUSH_ROUTE_CONTRACTS.items():
        register_contract(handler_name, contract)


__all__ = [
    "WEB_PUSH_ROUTE_CONTRACTS",
    "PushSubscribeBody",
    "PushUnsubscribeBody",
    "register_web_push_contracts",
    "web_push_status_route",
    "web_push_subscribe_route",
    "web_push_unsubscribe_route",
]
