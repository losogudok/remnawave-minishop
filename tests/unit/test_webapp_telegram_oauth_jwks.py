"""The Telegram OAuth callback runs while the customer's browser waits on a redirect.

Every network call it makes is therefore bounded and, where possible, made once instead of once
per login — an unbounded fetch here surfaces to the customer as a 504 from the reverse proxy,
with no way back into the app.
"""

import time
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import jwt

from bot.app.web import webapp_auth

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENT_ID = 8353391008
NONCE = "nonce-value"


class _FakeSigningKey:
    key = "fake-signing-key"


def _fake_claims(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "nonce": NONCE,
        "iat": int(time.time()),
        "id": 4242,
        "given_name": "Alex",
    }


class TelegramOauthJwksTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.constructed: list[dict[str, Any]] = []
        self.get_signing_key = lambda _token: _FakeSigningKey()
        constructed = self.constructed
        test_case = self

        class FakeJWKClient:
            def __init__(self, uri: str, **kwargs: Any) -> None:
                constructed.append({"uri": uri, **kwargs})

            def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
                return test_case.get_signing_key(token)

        for patcher in (
            patch.object(webapp_auth, "_telegram_jwks_client", None),
            patch.object(jwt, "PyJWKClient", FakeJWKClient),
            patch.object(jwt, "decode", _fake_claims),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _validate(self) -> dict[str, Any] | None:
        claims: dict[str, Any] | None = await webapp_auth.validate_telegram_oauth_id_token(
            "header.payload.signature",
            client_id=CLIENT_ID,
            expected_nonce=NONCE,
            max_age_seconds=300,
        )
        return claims

    async def test_signing_keys_are_fetched_once_instead_of_once_per_login(self) -> None:
        first = await self._validate()
        second = await self._validate()

        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        assert first is not None
        self.assertEqual(first["id"], 4242)
        self.assertEqual(
            len(self.constructed),
            1,
            "a new JWKS client per login refetches the key set on every login",
        )

    async def test_jwks_client_is_built_with_a_cache_and_a_timeout(self) -> None:
        await self._validate()

        self.assertEqual(self.constructed[0]["uri"], webapp_auth.TELEGRAM_OAUTH_JWKS_URL)
        self.assertIs(self.constructed[0]["cache_jwk_set"], True)
        self.assertEqual(
            self.constructed[0]["lifespan"],
            webapp_auth.TELEGRAM_OAUTH_JWKS_LIFESPAN_SECONDS,
        )
        self.assertEqual(
            self.constructed[0]["timeout"],
            webapp_auth.TELEGRAM_OAUTH_JWKS_TIMEOUT_SECONDS,
        )

    async def test_a_stalled_key_fetch_gives_up_instead_of_hanging_the_login(self) -> None:
        def stalled(_token: str) -> _FakeSigningKey:
            time.sleep(0.5)
            return _FakeSigningKey()

        self.get_signing_key = stalled
        with patch.object(webapp_auth, "TELEGRAM_OAUTH_VALIDATION_TIMEOUT_SECONDS", 0.05):
            started = time.monotonic()
            result = await self._validate()
            elapsed = time.monotonic() - started

        self.assertIsNone(result)
        self.assertLess(elapsed, 0.4, "the login waited for the stalled key fetch")


def test_notification_probe_cannot_outlive_the_login_redirect() -> None:
    source = (REPO_ROOT / "backend/bot/app/web/webapp/auth_oauth.py").read_text(encoding="utf-8")

    probe_call = source.index("_probe_telegram_notifications_for_user_id(request, int(")
    guarded = source[source.index("if final_user_id:", 0, probe_call) : probe_call]

    assert "asyncio.wait_for(" in guarded
    assert "TELEGRAM_NOTIFICATIONS_PROBE_TIMEOUT_SECONDS" in source
