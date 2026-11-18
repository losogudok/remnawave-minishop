"""Native exchanges exercise real JWT verification and the browser security middleware."""

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import jwt
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import rsa

from bot.app.web import context, webapp_auth
from bot.app.web.webapp import (
    assets,
    auth_oauth,
    external_oauth,
    external_oauth_providers,
    native_auth,
    native_auth_challenges,
)
from bot.app.web.webapp.native_auth_contracts import NativeProvider
from config.settings import Settings
from tests.unit.test_external_oauth import _profile, _provider, _ScalarResult, _SessionFactory

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_JWK = {**jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True), "kid": "test"}


class _Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.fail = False

    async def set(self, name: str, value: str, *, ex: int, nx: bool) -> bool:
        if self.fail:
            raise ConnectionError("test Redis unavailable")
        assert ex == 300 and nx
        self.values[name] = value
        return True

    async def get(self, name: str) -> str | None:
        if self.fail:
            raise ConnectionError("test Redis unavailable")
        return self.values.get(name)

    async def eval(self, script: str, numkeys: int, *args: str) -> int:
        if self.fail:
            raise ConnectionError("test Redis unavailable")
        assert script == native_auth_challenges._CONSUME and numkeys == 1
        key, nonce = args
        if self.values.get(key) != nonce:
            return 0
        del self.values[key]
        return 1


class NativeOauthTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.settings = Settings(
            _env_file=None,
            BOT_TOKEN="42:placeholder",
            POSTGRES_USER="test",
            POSTGRES_PASSWORD="test",
            WEBAPP_SESSION_SECRET="native-test-session-secret",
            SUBSCRIPTION_MINI_APP_URL="https://app.example.test",
            GOOGLE_OIDC_ENABLED=True,
            GOOGLE_OIDC_CLIENT_ID="google-client",
            GOOGLE_OIDC_CLIENT_SECRET="placeholder",
            TELEGRAM_OAUTH_CLIENT_ID=42,
        )
        self.redis = _Redis()
        self.limit = AsyncMock(return_value=None)
        self.google_complete = AsyncMock(
            return_value=web.json_response({"ok": True, "token": "session", "user_id": 77})
        )
        self.telegram_complete = AsyncMock(
            return_value=web.json_response({"ok": True, "token": "session", "user_id": 77})
        )
        jwks_response = AsyncMock()
        jwks_response.status = 200
        jwks_response.json.return_value = {"keys": [_JWK]}
        jwks_context = AsyncMock()
        jwks_context.__aenter__.return_value = jwks_response
        google_http = AsyncMock()
        google_http.get = Mock(return_value=jwks_context)
        http_context = AsyncMock()
        http_context.__aenter__.return_value = google_http
        for patcher in (
            patch.object(native_auth_challenges, "get_redis", AsyncMock(return_value=self.redis)),
            patch.object(native_auth, "check_request_limits", self.limit),
            patch.object(external_oauth, "complete_native_google_login", self.google_complete),
            patch.object(auth_oauth, "complete_telegram_login", self.telegram_complete),
            patch.object(external_oauth_providers, "ClientSession", return_value=http_context),
            patch.object(
                webapp_auth,
                "_load_telegram_oauth_signing_key",
                AsyncMock(return_value=jwt.PyJWK.from_dict(_JWK)),
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        app = web.Application(middlewares=[assets._csrf_protection_middleware])
        app[context.SETTINGS] = self.settings
        for path, handler in (
            ("/api/auth/google/challenge", native_auth.native_google_challenge_route),
            ("/api/auth/google/token", native_auth.native_google_token_route),
            ("/api/auth/telegram/native/challenge", native_auth.native_telegram_challenge_route),
            ("/api/auth/telegram/native/token", native_auth.native_telegram_token_route),
        ):
            app.router.add_post(path, handler)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)

    def _path(self, provider: NativeProvider) -> str:
        return "/api/auth/google" if provider == "google" else "/api/auth/telegram/native"

    async def _challenge(self, provider: NativeProvider) -> dict[str, Any]:
        response = await self.client.post(self._path(provider) + "/challenge", json={})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", response.headers)
        payload: dict[str, Any] = await response.json()
        self.assertEqual(payload["expires_in"], 300)
        self.assertRegex(payload["nonce"], r"^[A-Za-z0-9_-]{43}$")
        return payload

    def _jwt(self, provider: NativeProvider, expected_nonce: str, **overrides: Any) -> str:
        now = int(time.time())
        claims = {
            "iss": "https://accounts.google.com"
            if provider == "google"
            else "https://oauth.telegram.org",
            "aud": "google-client" if provider == "google" else "42",
            "exp": now + 300,
            "iat": now,
            "sub": "google-subject" if provider == "google" else "42",
            "id": 42,
            "nonce": expected_nonce,
            "email": "native@gmail.com",
            "email_verified": True,
        }
        claims.update(overrides)
        return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": "test"})

    async def test_success_is_sessionless_and_replay_cannot_create_another_session(self) -> None:
        for provider in ("google", "telegram"):
            challenge = await self._challenge(provider)
            payload = {
                "request_id": challenge["request_id"],
                "id_token": self._jwt(provider, challenge["nonce"]),
            }
            response = await self.client.post(self._path(provider) + "/token", json=payload)
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["user_id"], 77)
            self.assertNotIn("Set-Cookie", response.headers)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            replay = await self.client.post(self._path(provider) + "/token", json=payload)
            self.assertEqual(replay.status, 410)
        self.google_complete.assert_awaited_once()
        self.telegram_complete.assert_awaited_once()
        self.assertEqual(self.redis.values, {})

    async def test_invalid_claims_or_signature_leave_challenge_unconsumed(self) -> None:
        for provider in ("google", "telegram"):
            for overrides in (
                {"iss": "https://attacker.test"},
                {"aud": "other-client"},
                {"exp": int(time.time()) - 1},
                {"nonce": "different"},
                {"nonce": None},
                {"iat": int(time.time()) + 600},
            ):
                challenge = await self._challenge(provider)
                token = self._jwt(provider, challenge["nonce"], **overrides)
                response = await self.client.post(
                    self._path(provider) + "/token",
                    json={
                        "request_id": challenge["request_id"],
                        "id_token": token,
                    },
                )
                self.assertEqual(response.status, 401, (provider, overrides))
                self.assertIn(
                    native_auth_challenges.challenge_key(
                        self.settings, provider, challenge["request_id"]
                    ),
                    self.redis.values,
                )
            challenge = await self._challenge(provider)
            claims = jwt.decode(
                self._jwt(provider, challenge["nonce"]), options={"verify_signature": False}
            )
            for token in (
                jwt.encode(claims, _OTHER_KEY, algorithm="RS256", headers={"kid": "test"}),
                jwt.encode(claims, "test-key-" * 8, algorithm="HS256", headers={"kid": "test"}),
            ):
                response = await self.client.post(
                    self._path(provider) + "/token",
                    json={
                        "request_id": challenge["request_id"],
                        "id_token": token,
                    },
                )
                self.assertEqual(response.status, 401)
        self.google_complete.assert_not_awaited()
        self.telegram_complete.assert_not_awaited()

    async def test_challenges_are_provider_bound(self) -> None:
        challenge = await self._challenge("google")
        response = await self.client.post(
            self._path("telegram") + "/token",
            json={
                "request_id": challenge["request_id"],
                "id_token": self._jwt("telegram", challenge["nonce"]),
            },
        )
        self.assertEqual(response.status, 410)
        self.telegram_complete.assert_not_awaited()

    async def test_native_telegram_audience_can_differ_from_web(self) -> None:
        self.settings.TELEGRAM_NATIVE_OAUTH_CLIENT_ID = 99
        challenge = await self._challenge("telegram")
        payload = {
            "request_id": challenge["request_id"],
            "id_token": self._jwt("telegram", challenge["nonce"]),
        }
        response = await self.client.post(self._path("telegram") + "/token", json=payload)
        self.assertEqual(response.status, 401)
        payload["id_token"] = self._jwt("telegram", challenge["nonce"], aud="99")
        response = await self.client.post(self._path("telegram") + "/token", json=payload)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.settings.TELEGRAM_OAUTH_CLIENT_ID, 42)

    async def test_concurrent_validations_have_one_winner(self) -> None:
        for provider, validator in (
            ("google", "google_profile"),
            ("telegram", "validate_telegram_oauth_id_token"),
        ):
            challenge = await self._challenge(provider)
            barrier = asyncio.Barrier(2)

            async def validate(
                *args: Any, barrier: asyncio.Barrier = barrier, **kwargs: Any
            ) -> dict[str, Any]:
                await barrier.wait()
                return {"subject": "google-subject", "id": 42}

            with patch.object(native_auth, validator, validate):
                responses = await asyncio.gather(
                    *(
                        self.client.post(
                            self._path(provider) + "/token",
                            json={
                                "request_id": challenge["request_id"],
                                "id_token": "token",
                            },
                        )
                        for _ in range(2)
                    )
                )
            self.assertEqual(sorted(r.status for r in responses), [200, 410])
        self.google_complete.assert_awaited_once()
        self.telegram_complete.assert_awaited_once()

    async def test_expiry_during_validation_and_redis_errors_fail_closed(self) -> None:
        for provider, validator in (
            ("google", "google_profile"),
            ("telegram", "validate_telegram_oauth_id_token"),
        ):
            for failure in ("expired", "redis"):
                challenge = await self._challenge(provider)

                async def validate(
                    *args: Any, failure: str = failure, **kwargs: Any
                ) -> dict[str, Any]:
                    if failure == "expired":
                        self.redis.values.clear()
                    else:
                        self.redis.fail = True
                    return {"subject": "google-subject", "id": 42}

                with patch.object(native_auth, validator, validate):
                    response = await self.client.post(
                        self._path(provider) + "/token",
                        json={
                            "request_id": challenge["request_id"],
                            "id_token": "token",
                        },
                    )
                self.assertEqual(response.status, 410 if failure == "expired" else 503)
                self.redis.fail = False
        self.google_complete.assert_not_awaited()
        self.telegram_complete.assert_not_awaited()

    async def test_missing_redis_and_read_failure_never_create_a_session(self) -> None:
        with patch.object(native_auth_challenges, "get_redis", AsyncMock(return_value=None)):
            response = await self.client.post(self._path("google") + "/challenge", json={})
            self.assertEqual(response.status, 503)
        challenge = await self._challenge("telegram")
        self.redis.fail = True
        response = await self.client.post(
            self._path("telegram") + "/token",
            json={
                "request_id": challenge["request_id"],
                "id_token": "token",
            },
        )
        self.assertEqual(response.status, 503)
        self.telegram_complete.assert_not_awaited()

    async def test_native_routes_reject_inherited_identity_and_browser_requests(self) -> None:
        for provider in ("google", "telegram"):
            for header, value in (
                ("Authorization", "Bearer old-session"),
                ("Cookie", "other=value"),
                ("Origin", "https://app.example.test"),
            ):
                for suffix in ("/challenge", "/token"):
                    response = await self.client.post(
                        self._path(provider) + suffix, json={}, headers={header: value}
                    )
                    self.assertEqual(response.status, 403)
        self.assertEqual(self.redis.values, {})

    async def test_bounded_strict_payload_and_configuration(self) -> None:
        for provider in ("google", "telegram"):
            for data, expected in (
                ("{", 400),
                ("[]", 400),
                ('{"purpose":"link"}', 400),
                ('{"padding":"' + "x" * 17000 + '"}', 413),
            ):
                response = await self.client.post(
                    self._path(provider) + "/challenge",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status, expected)
            for payload in (
                {"id_token": "x"},
                {"request_id": "../bad", "id_token": "x"},
                {"request_id": "a" * 43, "id_token": "x", "user_id": 7},
            ):
                response = await self.client.post(self._path(provider) + "/token", json=payload)
                self.assertEqual(response.status, 400)
        self.settings.GOOGLE_OIDC_ENABLED = False
        response = await self.client.post(self._path("google") + "/challenge", json={})
        self.assertEqual(response.status, 404)
        self.settings.TELEGRAM_OAUTH_CLIENT_ID = None
        response = await self.client.post(self._path("telegram") + "/challenge", json={})
        self.assertEqual(response.status, 503)

    async def test_ip_limits_apply_before_challenge_storage(self) -> None:
        self.limit.return_value = web.json_response(
            {"ok": False, "error": "rate_limited"}, status=429, headers={"Retry-After": "60"}
        )
        response = await self.client.post(self._path("google") + "/challenge", json={})
        self.assertEqual(response.status, 429)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.redis.values, {})


class NativeAccountCompletionTests(IsolatedAsyncioTestCase):
    async def test_google_existing_identity_returns_a_native_session(self) -> None:
        factory = _SessionFactory()
        identity = SimpleNamespace(user_id=77)
        factory.session.execute.return_value = _ScalarResult(identity)
        user = SimpleNamespace(
            user_id=77,
            is_banned=False,
            email="native@gmail.com",
            notification_email="native@gmail.com",
            first_name="Native User",
        )
        settings = SimpleNamespace(
            DEFAULT_LANGUAGE="en",
            WEBAPP_SESSION_SECRET="test",
            WEBAPP_SESSION_TTL_SECONDS=3600,
        )
        with (
            patch.multiple(
                external_oauth,
                get_settings=Mock(return_value=settings),
                get_session_factory=Mock(return_value=factory),
                _verified_email_owner=AsyncMock(return_value=None),
                _apply_referral_to_existing_user=AsyncMock(return_value=False),
                _sync_panel_identity_for_user=AsyncMock(),
                _invalidate_webapp_user_caches=AsyncMock(),
            ),
            patch.object(external_oauth.user_dal, "get_user_by_id", AsyncMock(return_value=user)),
            patch.object(external_oauth.user_email_dal, "upsert_user_email_address", AsyncMock()),
        ):
            response = await external_oauth.complete_native_google_login(
                SimpleNamespace(), _provider(), _profile(email=user.email)
            )
        self.assertEqual(response.status, 200)
        factory.session.commit.assert_awaited_once()
        body = json.loads(response.text or "{}")
        self.assertEqual(body["user_id"], 77)
        self.assertEqual(webapp_auth.verify_webapp_session_token(settings, body["token"]), 77)
        self.assertFalse(response.cookies)

    async def test_banned_accounts_cannot_obtain_native_sessions(self) -> None:
        for provider in ("google", "telegram"):
            factory = _SessionFactory()
            user = SimpleNamespace(user_id=77, is_banned=True)
            factory.session.execute.return_value = _ScalarResult(SimpleNamespace(user_id=77))
            module = external_oauth if provider == "google" else auth_oauth
            with (
                patch.multiple(
                    module,
                    get_settings=Mock(return_value=SimpleNamespace(DEFAULT_LANGUAGE="en")),
                    get_session_factory=Mock(return_value=factory),
                ),
                patch.object(external_oauth, "_verified_email_owner", AsyncMock(return_value=None)),
                patch.object(
                    external_oauth.user_dal, "get_user_by_id", AsyncMock(return_value=user)
                ),
                patch.object(
                    auth_oauth, "_ensure_user_from_telegram", AsyncMock(return_value=user)
                ),
                patch.object(module, "create_webapp_session_token") as mint,
            ):
                if provider == "google":
                    response = await external_oauth.complete_native_google_login(
                        SimpleNamespace(), _provider(), _profile()
                    )
                else:
                    response = await auth_oauth.complete_telegram_login(
                        SimpleNamespace(),
                        {"id": 42},
                        auth_payload=auth_oauth.WebAppTelegramAuthPayload(),
                        native=True,
                    )
            self.assertEqual(response.status, 403)
            mint.assert_not_called()
            factory.session.commit.assert_not_awaited()

    async def test_google_requires_email_confirmation_without_sending_email_or_session(
        self,
    ) -> None:
        factory = _SessionFactory()
        email = "user@thirdparty.test"
        owner = SimpleNamespace(user_id=77, is_banned=False)
        with (
            patch.multiple(
                external_oauth,
                get_settings=Mock(return_value=SimpleNamespace(DEFAULT_LANGUAGE="en")),
                get_session_factory=Mock(return_value=factory),
                _verified_email_owner=AsyncMock(return_value=owner),
            ),
            patch.object(
                external_oauth.user_email_dal, "ensure_primary_user_email_address", AsyncMock()
            ),
            patch.object(external_oauth, "get_email_auth_service") as email_service,
            patch.object(external_oauth, "create_webapp_session_token") as mint,
        ):
            response = await external_oauth.complete_native_google_login(
                SimpleNamespace(), _provider(), _profile(email=email)
            )
        self.assertEqual(response.status, 409)
        self.assertEqual(json.loads(response.text or "{}")["error"], "email_confirmation_required")
        email_service.assert_not_called()
        mint.assert_not_called()
        factory.session.commit.assert_not_awaited()
        self.assertFalse(response.cookies)

    async def test_telegram_completion_commits_before_minting_and_returns_no_cookies(self) -> None:
        factory = _SessionFactory()
        user = SimpleNamespace(user_id=77, is_banned=False)
        settings = SimpleNamespace(WEBAPP_SESSION_SECRET="test", WEBAPP_SESSION_TTL_SECONDS=3600)
        with patch.multiple(
            auth_oauth,
            get_settings=Mock(return_value=settings),
            get_session_factory=Mock(return_value=factory),
            _ensure_user_from_telegram=AsyncMock(return_value=user),
            _apply_referral_to_existing_user=AsyncMock(return_value=False),
            _apply_ad_attribution_if_needed=AsyncMock(),
            _invalidate_webapp_user_caches=AsyncMock(),
            _probe_telegram_notifications_for_user_id=AsyncMock(),
        ):
            response = await auth_oauth.complete_telegram_login(
                SimpleNamespace(),
                {"id": 42},
                auth_payload=auth_oauth.WebAppTelegramAuthPayload(),
                native=True,
            )
        factory.session.commit.assert_awaited_once()
        body = json.loads(response.text or "{}")
        self.assertEqual(body["user_id"], 77)
        self.assertEqual(webapp_auth.verify_webapp_session_token(settings, body["token"]), 77)
        self.assertFalse(response.cookies)
