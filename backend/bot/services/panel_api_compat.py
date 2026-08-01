"""Compatibility helpers for Remnawave 2.8 and 3.x user identities.

Remnawave 3.0 removed ``uuid`` from user objects and made the numeric ``id``
the only user identifier accepted by user-scoped API routes and payloads.
Mini Shop deliberately keeps its historical internal names (``uuid`` keys,
``panel_user_uuid`` database columns, and public service methods) so upgrades
do not require an eager local database migration.  At this API boundary, a
3.x ``id`` is exposed internally as a decimal-string ``uuid`` compatibility
alias; outbound requests translate that alias back to an integer ``id``.

Do not use these helpers for node, squad, host, or subscription UUIDs.  Those
identifiers remain UUIDs in Remnawave 3.x.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class PanelUserIdMode(Enum):
    UUID = "uuid"
    NUMERIC_ID = "numeric_id"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PanelApiCompatibility:
    version: str | None
    user_id_mode: PanelUserIdMode

    @classmethod
    def unknown(cls) -> "PanelApiCompatibility":
        return cls(version=None, user_id_mode=PanelUserIdMode.UNKNOWN)

    @classmethod
    def from_metadata(cls, payload: object) -> "PanelApiCompatibility":
        """Parse ``GET /system/metadata`` from supported panel generations."""
        if not isinstance(payload, dict):
            return cls.unknown()
        response = payload.get("response")
        source = response if isinstance(response, dict) else payload
        raw_version = source.get("version")
        version = str(raw_version or "").strip()
        match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", version)
        if not match:
            return cls.unknown()
        major = int(match.group(1))
        return cls(
            version=version,
            user_id_mode=(PanelUserIdMode.NUMERIC_ID if major >= 3 else PanelUserIdMode.UUID),
        )


def numeric_panel_user_id(value: object) -> int | None:
    """Return a valid 3.x user id, rejecting booleans, zero, and UUIDs."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    if not text.isdecimal():
        return None
    parsed = int(text)
    return parsed if parsed > 0 else None


def normalize_panel_user(value: object) -> dict[str, Any] | None:
    """Return a copy with Mini Shop's historical ``uuid`` identity contract."""
    if not isinstance(value, dict):
        return None
    user = dict(value)
    legacy_uuid = str(user.get("uuid") or "").strip()
    if legacy_uuid:
        user["uuid"] = legacy_uuid
        return user
    numeric_id = numeric_panel_user_id(user.get("id"))
    if numeric_id is not None:
        user["uuid"] = str(numeric_id)
    return user


def normalize_panel_users(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalized for value in values if (normalized := normalize_panel_user(value))]
