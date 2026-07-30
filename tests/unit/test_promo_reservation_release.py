from __future__ import annotations

import asyncio
from typing import Any, cast

from sqlalchemy import create_engine, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from db.dal import promo_code_dal
from db.models import Payment, PromoCode, User


class _AsyncSessionAdapter:
    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: Any) -> Any:
        return self._session.execute(statement)


def test_terminal_payment_releases_personal_promo_reservation_for_retry() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    tables = (User.__table__, PromoCode.__table__, Payment.__table__)
    User.metadata.create_all(engine, tables=tables)

    with engine.begin() as connection:
        connection.execute(
            insert(User).values(
                user_id=42,
                telegram_notifications_status="unknown",
            )
        )
        connection.execute(
            insert(PromoCode).values(
                promo_code_id=5,
                code="PERSONAL10",
                bonus_days=0,
                bonus_requires_payment=False,
                applies_to="all",
                origin="test",
                user_id=42,
                max_activations=1,
                current_activations=0,
                is_active=True,
            )
        )
        connection.execute(
            insert(Payment).values(
                payment_id=10,
                user_id=42,
                provider_payment_id="link-10",
                provider_payment_url="https://pay.example/10",
                provider="wata",
                amount=90,
                currency="RUB",
                status="pending_wata",
                promo_code_id=5,
            )
        )

    with Session(engine) as sync_session:
        session = cast(AsyncSession, _AsyncSessionAdapter(sync_session))

        assert asyncio.run(promo_code_dal.user_has_pending_payment_with_promo(session, 42, 5))

        sync_session.execute(
            update(Payment).where(Payment.payment_id == 10).values(status="canceled")
        )
        sync_session.commit()

        assert not asyncio.run(promo_code_dal.user_has_pending_payment_with_promo(session, 42, 5))
        assert asyncio.run(promo_code_dal.count_pending_payments_with_promo(session, 5)) == 0

        sync_session.execute(
            update(Payment).where(Payment.payment_id == 10).values(status="failed")
        )
        sync_session.commit()

        assert not asyncio.run(promo_code_dal.user_has_pending_payment_with_promo(session, 42, 5))

    engine.dispose()
