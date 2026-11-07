"""Preserve setup completion and claim history when account identities merge."""

import json

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.setup_reward_models import SetupReward


def _row_history(row: SetupReward, original_user_id: int) -> dict[str, object]:
    return {
        "user_id": original_user_id,
        "reward_type": row.reward_type,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "accepted_days": row.accepted_days,
        "subscription_id": row.subscription_id,
        "target_end_date": row.target_end_date.isoformat() if row.target_end_date else None,
        "confirmed_end_date": (
            row.confirmed_end_date.isoformat() if row.confirmed_end_date else None
        ),
        "state": row.state,
    }


def _history(row: SetupReward) -> list[dict[str, object]]:
    try:
        value = json.loads(str(row.history_json or "[]"))
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


async def merge_setup_rewards(
    session: AsyncSession,
    source_user_id: int,
    target_user_id: int,
    *,
    source_subscription_id: int | None = None,
    surviving_subscription_id: int | None = None,
) -> None:
    source_rows = list(
        (await session.execute(select(SetupReward).where(SetupReward.user_id == source_user_id)))
        .scalars()
        .all()
    )
    for source in source_rows:
        source_history_entry = _row_history(source, source_user_id)
        target = await session.scalar(
            select(SetupReward).where(
                SetupReward.user_id == target_user_id,
                SetupReward.reward_type == source.reward_type,
            )
        )
        if target is None:
            if (
                source.state == "pending"
                and source_subscription_id == source.subscription_id
                and surviving_subscription_id is not None
            ):
                # Move the pending obligation to the surviving subscription,
                # while leaving the merge's established expiry calculation intact.
                source.subscription_id = surviving_subscription_id
            history = [*_history(source), source_history_entry]
            await session.execute(
                update(SetupReward)
                .where(SetupReward.id == source.id)
                .values(user_id=target_user_id, history_json=json.dumps(history))
            )
            continue
        target_history = [*_history(target), _row_history(target, target_user_id)]
        source_history = [*_history(source), source_history_entry]
        target.history_json = json.dumps([*target_history, *source_history])
        if target.accepted_days is None and source.accepted_days is not None:
            target.accepted_days = source.accepted_days
            target.subscription_id = (
                surviving_subscription_id
                if source.state == "pending"
                and source.subscription_id == source_subscription_id
                and surviving_subscription_id is not None
                else source.subscription_id
            )
            target.target_end_date = source.target_end_date
            target.confirmed_end_date = source.confirmed_end_date
            target.state = source.state
        await session.delete(source)
