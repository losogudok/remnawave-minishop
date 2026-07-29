from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import main_worker
from sqlalchemy.dialects import postgresql

from bot.services.wata_reconciliation_worker import WataReconciliationWorker
from db.dal import payment_dal, wata_reconciliation_dal


class _SessionContext:
    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _SessionFactory:
    def __init__(self) -> None:
        self.sessions: list[AsyncMock] = []

    def __call__(self) -> _SessionContext:
        session = AsyncMock()
        self.sessions.append(session)
        return _SessionContext(session)


def _payment(payment_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(
        payment_id=payment_id,
        user_id=1001,
        provider="wata",
        provider_payment_id=f"link-{payment_id}",
        provider_payment_url=f"https://pay.example/{payment_id}",
        status="pending_wata",
        created_at=datetime.now(UTC) - timedelta(minutes=20),
    )


class WataReconciliationWorkerTests(IsolatedAsyncioTestCase):
    def _worker(self) -> tuple[WataReconciliationWorker, Any, _SessionFactory]:
        service = SimpleNamespace(
            configured=True,
            payment_link_expired_locally=AsyncMock(),
            refresh_payment_status=AsyncMock(),
        )
        session_factory = _SessionFactory()
        worker = WataReconciliationWorker(
            cast(Any, SimpleNamespace()),
            cast(Any, session_factory),
            cast(Any, service),
        )
        return worker, service, session_factory

    async def test_expired_candidate_is_reloaded_and_refreshed(self) -> None:
        worker, service, session_factory = self._worker()
        payment = _payment()
        service.payment_link_expired_locally = lambda *_args, **_kwargs: True

        with (
            patch.object(
                wata_reconciliation_dal,
                "list_candidates",
                AsyncMock(return_value=[payment]),
            ),
            patch.object(
                payment_dal,
                "get_payment_by_db_id",
                AsyncMock(return_value=payment),
            ) as get_payment,
        ):
            await worker.tick()

        self.assertEqual(len(session_factory.sessions), 2)
        get_payment.assert_awaited_once_with(
            session_factory.sessions[1],
            42,
            fresh=True,
        )
        session_factory.sessions[1].rollback.assert_awaited_once()
        service.refresh_payment_status.assert_awaited_once()
        refresh_session, refresh_payment = service.refresh_payment_status.await_args.args
        self.assertIs(refresh_session, session_factory.sessions[1])
        self.assertEqual(refresh_payment.payment_id, 42)

    async def test_unexpired_candidate_is_not_refreshed(self) -> None:
        worker, service, session_factory = self._worker()
        service.payment_link_expired_locally = lambda *_args, **_kwargs: False

        with patch.object(
            wata_reconciliation_dal,
            "list_candidates",
            AsyncMock(return_value=[_payment()]),
        ):
            await worker.tick()

        self.assertEqual(len(session_factory.sessions), 1)
        service.refresh_payment_status.assert_not_awaited()


class WataReconciliationDalTests(IsolatedAsyncioTestCase):
    async def test_candidate_query_is_bounded_and_requires_a_saved_link(self) -> None:
        payment = _payment()
        result = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [payment]),
        )
        session = AsyncMock()
        session.execute.return_value = result

        candidates = await wata_reconciliation_dal.list_candidates(
            session,
            limit=25,
            grace_seconds=60,
        )

        self.assertEqual(candidates, [payment])
        statement = session.execute.await_args.args[0]
        sql = str(
            statement.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        ).lower()
        self.assertIn("wata_crypto", sql)
        self.assertIn("pending_wata", sql)
        self.assertIn("payments.provider_payment_id is not null", sql)
        self.assertIn("payments.provider_payment_url is not null", sql)
        self.assertIn("order by payments.created_at asc, payments.payment_id asc", sql)
        self.assertIn("limit 25", sql)


def test_core_worker_registers_wata_reconciliation() -> None:
    assert "WataReconciliationWorker" in {task.name for task in main_worker._core_worker_tasks()}
