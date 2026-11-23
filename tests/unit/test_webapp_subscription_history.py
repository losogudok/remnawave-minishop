import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

import bot.app.web.subscription_webapp  # noqa: F401
from bot.app.web.webapp.serializers import _serialize_subscription
from config.settings import Settings
from db.dal.subscription_dal import get_latest_subscription_by_user_id


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        BOT_TOKEN="token",
        POSTGRES_USER="u",
        POSTGRES_PASSWORD="p",
    )


def test_inactive_subscription_without_history_stays_neutral() -> None:
    payload = _serialize_subscription(_settings(), None, None, "en")

    assert payload["active"] is False
    assert payload["status"] == "INACTIVE"
    assert payload["end_date"] is None
    assert payload["end_date_text"] is None


@pytest.mark.parametrize(
    ("panel_connected", "snapshot_connected", "expected"),
    [(True, False, True), (False, True, True), (False, False, False)],
)
def test_active_subscription_exposes_confirmed_vpn_connection(
    panel_connected: bool, snapshot_connected: bool, expected: bool
) -> None:
    now = datetime.now(UTC)
    active = {"end_date": now + timedelta(days=1), "has_connected_to_vpn": panel_connected}
    local_sub = SimpleNamespace(last_connected_at=now if snapshot_connected else None)

    payload = _serialize_subscription(_settings(), active, local_sub, "en")

    assert payload["active"] is True
    assert payload["has_connected_to_vpn"] is expected


def test_inactive_subscription_with_past_end_date_is_expired() -> None:
    end_date = datetime.now(UTC) - timedelta(days=1)
    local_sub = SimpleNamespace(
        end_date=end_date,
        status_from_panel="ACTIVE",
    )

    payload = _serialize_subscription(_settings(), None, local_sub, "en")

    assert payload["active"] is False
    assert payload["status"] == "EXPIRED"
    assert payload["end_date"] == end_date.isoformat()
    assert payload["end_date_text"] == end_date.strftime("%d.%m.%Y %H:%M")


def test_expired_panel_status_wins_over_future_end_date() -> None:
    end_date = datetime.now(UTC) + timedelta(days=1)
    local_sub = SimpleNamespace(
        end_date=end_date,
        status_from_panel="EXPIRED",
    )

    payload = _serialize_subscription(_settings(), None, local_sub, "en")

    assert payload["status"] == "EXPIRED"


def test_latest_subscription_lookup_includes_inactive_history() -> None:
    expected = SimpleNamespace(subscription_id=9)
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: expected))
        )
    )

    result = asyncio.run(
        get_latest_subscription_by_user_id(
            cast(AsyncSession, session),
            user_id=42,
            panel_user_uuid="panel-user",
        )
    )

    assert result is expected
    statement = session.execute.await_args.args[0]
    rendered = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "subscriptions.user_id = 42" in rendered
    assert "subscriptions.panel_user_uuid = 'panel-user'" in rendered
    where_clause = rendered.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "subscriptions.is_active" not in where_clause
    assert "ORDER BY subscriptions.end_date DESC, subscriptions.subscription_id DESC" in rendered
