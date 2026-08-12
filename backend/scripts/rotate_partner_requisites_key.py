# SQLAlchemy legacy Column declarations expose instance attributes as Column[T].
# mypy: disable-error-code="assignment,arg-type"

"""Validate or atomically rotate encrypted partner withdrawal requisites.

Dry-run is the default. Set APPLY=1 together with
PARTNER_REQUISITES_NEW_ENCRYPTION_KEY and PARTNER_REQUISITES_NEW_KEY_ID to
write the re-encrypted values.
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import select

from bot.services.partner_common import compact_json
from bot.services.partner_withdrawal_service import (
    decrypt_partner_requisites,
    encrypt_partner_requisites,
    normalize_partner_encryption_key,
)
from config.settings import Settings
from db.dal import partner_dal
from db.database_setup import init_db_connection
from db.partner_models import PartnerWithdrawal


async def main() -> None:
    apply = os.environ.get("APPLY") == "1"
    new_raw_key = os.environ.get("PARTNER_REQUISITES_NEW_ENCRYPTION_KEY", "").strip()
    new_key_id = os.environ.get("PARTNER_REQUISITES_NEW_KEY_ID", "").strip()
    if not new_raw_key or not new_key_id:
        raise SystemExit(
            "PARTNER_REQUISITES_NEW_ENCRYPTION_KEY and PARTNER_REQUISITES_NEW_KEY_ID are required"
        )
    if len(new_key_id) > 32:
        raise SystemExit("PARTNER_REQUISITES_NEW_KEY_ID must be at most 32 characters")
    normalize_partner_encryption_key(new_raw_key)

    settings = Settings()
    old_secret = settings.PARTNER_REQUISITES_ENCRYPTION_KEY
    if not old_secret:
        raise SystemExit("PARTNER_REQUISITES_ENCRYPTION_KEY is required")
    old_raw_key = old_secret.get_secret_value()
    normalize_partner_encryption_key(old_raw_key)
    old_key_id = str(settings.PARTNER_REQUISITES_KEY_ID)
    if old_key_id == new_key_id:
        raise SystemExit("The new key id must differ from PARTNER_REQUISITES_KEY_ID")

    session_factory = init_db_connection(settings)
    async with session_factory() as session:
        withdrawals = list(
            (
                await session.execute(
                    select(PartnerWithdrawal)
                    .where(PartnerWithdrawal.requisites_ciphertext.is_not(None))
                    .order_by(PartnerWithdrawal.withdrawal_id)
                    .with_for_update()
                )
            ).scalars()
        )
        for withdrawal in withdrawals:
            if str(withdrawal.requisites_key_id) != old_key_id:
                raise SystemExit(
                    "Found requisites encrypted with an unexpected key id; "
                    "restore that key and rotate in sequence"
                )
            associated_data = (
                f"partner:{int(withdrawal.partner_id)}:{withdrawal.client_idempotency_key}"
            ).encode()
            payload = decrypt_partner_requisites(
                bytes(withdrawal.requisites_ciphertext),
                raw_key=old_raw_key,
                associated_data=associated_data,
            )
            if apply:
                withdrawal.requisites_ciphertext = encrypt_partner_requisites(
                    payload,
                    raw_key=new_raw_key,
                    associated_data=associated_data,
                )
                withdrawal.requisites_key_id = new_key_id

        if apply:
            await partner_dal.create_audit_event(
                session,
                event_type="withdrawal_requisites_key_rotated",
                actor_type="system",
                old_values_json=compact_json({"key_id": old_key_id}),
                new_values_json=compact_json(
                    {"key_id": new_key_id, "requisites_count": len(withdrawals)}
                ),
            )
            await session.commit()
        else:
            await session.rollback()

    mode = "rotated" if apply else "validated"
    print(f"{mode} requisites: {len(withdrawals)}; {old_key_id} -> {new_key_id}")


if __name__ == "__main__":
    asyncio.run(main())
