from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from pydantic import SecretStr

from bot.app.web import webapp_auth
from bot.app.web.webapp import auth_oauth, telegram_oauth_transport
from tests.support.settings_stub import settings_stub


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.payload = payload
        self.status = status

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def json(self, *, content_type: None = None) -> dict[str, Any]:
        del content_type
        return self.payload


class _RecordingSession:
    def __init__(self) -> None:
        self.closed = False
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append(("POST", url, kwargs))
        return _FakeResponse({"id_token": "telegram-id-token"})

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.requests.append(("GET", url, kwargs))
        return _FakeResponse({"keys": [{"kid": "current"}]})

    async def close(self) -> None:
        self.closed = True


def _proxy_settings(**overrides: Any) -> Any:
    values = {
        "TELEGRAM_BOT_PROXY_URL": SecretStr(
            "socks5://oauth-user:oauth-password@proxy.example.test:1080"
        ),
        "TELEGRAM_OAUTH_CLIENT_ID": 42,
        "TELEGRAM_OAUTH_CLIENT_SECRET": "client-secret",
        "TELEGRAM_OAUTH_USE_BOT_PROXY": True,
    }
    values.update(overrides)
    return settings_stub(**values)


def test_oauth_transport_uses_socks5_with_remote_dns_and_redacted_log(monkeypatch, caplog) -> None:
    settings = _proxy_settings()
    connector = object()
    connector_calls: list[tuple[str, bool]] = []
    created_sessions: list[_RecordingSession] = []

    def create_connector(url: str, *, rdns: bool) -> object:
        connector_calls.append((url, rdns))
        return connector

    def create_session(**kwargs: Any) -> _RecordingSession:
        assert kwargs["connector"] is connector
        created = _RecordingSession()
        created_sessions.append(created)
        return created

    monkeypatch.setattr(telegram_oauth_transport.ProxyConnector, "from_url", create_connector)
    monkeypatch.setattr(telegram_oauth_transport, "ClientSession", create_session)
    monkeypatch.setattr(telegram_oauth_transport, "_TELEGRAM_OAUTH_HTTP_SESSION", None)
    monkeypatch.setattr(telegram_oauth_transport, "_TELEGRAM_OAUTH_HTTP_SESSION_ROUTE_KEY", None)

    async def run() -> None:
        first = await telegram_oauth_transport.get_telegram_oauth_http_session(settings)
        second = await telegram_oauth_transport.get_telegram_oauth_http_session(settings)
        assert first is second
        await telegram_oauth_transport.close_telegram_oauth_http_session()

    with caplog.at_level("INFO"):
        asyncio.run(run())

    raw_proxy_url = settings.TELEGRAM_BOT_PROXY_URL.get_secret_value()
    assert connector_calls == [(raw_proxy_url, True)]
    assert len(created_sessions) == 1
    assert created_sessions[0].closed is True
    assert raw_proxy_url not in caplog.text
    assert "oauth-password" not in caplog.text
    assert "socks5://***:***@proxy.example.test:1080" in caplog.text


def test_oauth_transport_allows_explicit_opt_out(monkeypatch) -> None:
    settings = settings_stub(
        TELEGRAM_BOT_PROXY_URL=SecretStr("socks5://proxy.example.test:1080"),
        TELEGRAM_OAUTH_USE_BOT_PROXY=False,
    )
    created_connectors: list[object | None] = []

    def unexpected_connector(*_args: Any, **_kwargs: Any) -> object:
        raise AssertionError("The OAuth proxy must respect the explicit opt-out")

    def create_session(**kwargs: Any) -> _RecordingSession:
        created_connectors.append(kwargs["connector"])
        return _RecordingSession()

    monkeypatch.setattr(telegram_oauth_transport.ProxyConnector, "from_url", unexpected_connector)
    monkeypatch.setattr(telegram_oauth_transport, "ClientSession", create_session)
    monkeypatch.setattr(telegram_oauth_transport, "_TELEGRAM_OAUTH_HTTP_SESSION", None)
    monkeypatch.setattr(telegram_oauth_transport, "_TELEGRAM_OAUTH_HTTP_SESSION_ROUTE_KEY", None)

    async def run() -> None:
        await telegram_oauth_transport.get_telegram_oauth_http_session(settings)
        await telegram_oauth_transport.close_telegram_oauth_http_session()

    asyncio.run(run())

    assert created_connectors == [None]


def test_oauth_transport_stays_direct_when_no_proxy_is_configured() -> None:
    settings = settings_stub(TELEGRAM_OAUTH_USE_BOT_PROXY=True)

    assert telegram_oauth_transport.telegram_oauth_transport_route_key(settings) == "direct"


def test_oauth_token_exchange_uses_dedicated_transport(monkeypatch) -> None:
    settings = _proxy_settings()
    session = _RecordingSession()
    get_session = AsyncMock(return_value=session)
    monkeypatch.setattr(auth_oauth, "get_settings", lambda _request: settings)
    monkeypatch.setattr(auth_oauth, "get_telegram_oauth_http_session", get_session)

    result = asyncio.run(
        auth_oauth._exchange_telegram_oauth_code(
            object(),
            code="authorization-code",
            code_verifier="pkce-verifier",
            redirect_uri="https://app.example.test/auth/telegram/callback",
        )
    )

    assert result == {"id_token": "telegram-id-token"}
    get_session.assert_awaited_once_with(settings)
    assert session.requests[0][0:2] == ("POST", "https://oauth.telegram.org/token")


def test_oauth_token_exchange_redacts_proxy_setup_errors(monkeypatch, caplog) -> None:
    settings = _proxy_settings()
    raw_proxy_url = settings.TELEGRAM_BOT_PROXY_URL.get_secret_value()

    async def fail_to_create_session(_settings: Any) -> _RecordingSession:
        raise RuntimeError(f"Cannot connect through {raw_proxy_url}")

    monkeypatch.setattr(auth_oauth, "get_settings", lambda _request: settings)
    monkeypatch.setattr(
        auth_oauth,
        "get_telegram_oauth_http_session",
        fail_to_create_session,
    )

    with caplog.at_level("WARNING"):
        result = asyncio.run(
            auth_oauth._exchange_telegram_oauth_code(
                object(),
                code="authorization-code",
                code_verifier="pkce-verifier",
                redirect_uri="https://app.example.test/auth/telegram/callback",
            )
        )

    assert result is None
    assert raw_proxy_url not in caplog.text
    assert "oauth-password" not in caplog.text
    assert "socks5://***:***@proxy.example.test:1080" in caplog.text


def test_oauth_jwks_fetch_uses_dedicated_transport_and_bounded_timeout(monkeypatch) -> None:
    settings = _proxy_settings()
    session = _RecordingSession()
    get_session = AsyncMock(return_value=session)
    monkeypatch.setattr(webapp_auth, "get_telegram_oauth_http_session", get_session)

    payload = asyncio.run(webapp_auth._fetch_telegram_oauth_jwks(settings))

    assert payload == {"keys": [{"kid": "current"}]}
    get_session.assert_awaited_once_with(settings)
    method, url, kwargs = session.requests[0]
    assert method == "GET"
    assert url == webapp_auth.TELEGRAM_OAUTH_JWKS_URL
    assert kwargs["timeout"] == webapp_auth.TELEGRAM_OAUTH_JWKS_TIMEOUT_SECONDS
