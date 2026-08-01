import asyncio
import logging
from typing import TYPE_CHECKING, Any

from bot.services.panel_api_compat import numeric_panel_user_id
from bot.utils.happ_crypto import create_happ_crypt4_link

logger = logging.getLogger(__name__)

# Static endpoint prefixes used as log/metric labels instead of the raw request
# path. Endpoints embed user identifiers (telegram id, username, email, uuids),
# so logging the path verbatim would leak private data into log files; the
# label keeps only the constant prefix. Longest prefixes first so e.g.


def _json_dict(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


class PanelApiSquadMutationMixin:
    _V3_SQUAD_BULK_LIMIT = 1000

    if TYPE_CHECKING:

        async def _request(
            self, method: str, endpoint: str, log_full_response: bool = False, **kwargs: Any
        ) -> dict[str, Any] | None: ...
        async def _invalidate_squad_caches(self) -> None: ...
        async def _invalidate_user_cache(self, user_uuid: str | None) -> None: ...
        async def _invalidate_all_users_cache(self) -> None: ...
        async def get_user_by_uuid(
            self, user_uuid: str, log_response: bool = False, *, use_cache: bool = True
        ) -> dict[str, Any] | None: ...
        async def update_user_details_on_panel(
            self,
            user_uuid: str,
            update_payload: dict[str, Any],
            log_response: bool = False,
        ) -> dict[str, Any] | None: ...

    @staticmethod
    def _user_internal_squad_uuids(user: dict[str, Any]) -> list[str]:
        values = user.get("activeInternalSquads")
        if not isinstance(values, list):
            return []
        result: list[str] = []
        for value in values:
            raw_uuid = value.get("uuid") if isinstance(value, dict) else value
            squad_uuid = str(raw_uuid or "").strip()
            if squad_uuid and squad_uuid not in result:
                result.append(squad_uuid)
        return result

    async def _update_legacy_user_squad_membership(
        self, squad_uuid: str, user_uuid: str, *, add: bool
    ) -> bool:
        """Target one 2.8 user without calling its misleading all-users route."""
        user = await self.get_user_by_uuid(user_uuid, use_cache=False)
        if user is None:
            return False
        squads = self._user_internal_squad_uuids(user)
        if add and squad_uuid not in squads:
            squads.append(squad_uuid)
        elif not add:
            squads = [value for value in squads if value != squad_uuid]
        else:
            return True
        return (
            await self.update_user_details_on_panel(
                user_uuid,
                {"activeInternalSquads": squads},
            )
            is not None
        )

    async def add_users_to_internal_squad(self, squad_uuid: str, user_uuids: list[str]) -> bool:
        if not user_uuids:
            return True
        numeric_ids = [numeric_panel_user_id(value) for value in user_uuids]
        if all(value is not None for value in numeric_ids):
            endpoint = f"/internal-squads/{squad_uuid}/bulk-actions/add-many-users"
            resolved_ids = list(dict.fromkeys(value for value in numeric_ids if value is not None))
        elif any(value is not None for value in numeric_ids):
            logger.error("Cannot add a mixed list of Remnawave 2.x and 3.x user identifiers.")
            return False
        else:
            # In 2.8.1 the add-users route means *all* users and ignores a body.
            # PATCH each requested user instead; 3.0's add-many-users route is
            # the first targeted bulk contract.
            results = await asyncio.gather(
                *(
                    self._update_legacy_user_squad_membership(squad_uuid, user_uuid, add=True)
                    for user_uuid in user_uuids
                )
            )
            if all(results):
                await self._invalidate_squad_caches()
                return True
            logger.error("Failed to add one or more users to squad %s.", squad_uuid)
            return False
        for offset in range(0, len(resolved_ids), self._V3_SQUAD_BULK_LIMIT):
            response_data = await self._request(
                "POST",
                endpoint,
                json={"userIds": resolved_ids[offset : offset + self._V3_SQUAD_BULK_LIMIT]},
                log_full_response=False,
            )
            if not response_data or response_data.get("error"):
                logger.error(
                    "Failed to add users to squad %s. Response: %s", squad_uuid, response_data
                )
                return False
        await self._invalidate_squad_caches()
        for user_uuid in user_uuids:
            await self._invalidate_user_cache(user_uuid)
        await self._invalidate_all_users_cache()
        return True

    async def remove_users_from_internal_squad(
        self, squad_uuid: str, user_uuids: list[str]
    ) -> bool:
        if not user_uuids:
            return True
        numeric_ids = [numeric_panel_user_id(value) for value in user_uuids]
        if all(value is not None for value in numeric_ids):
            endpoint = f"/internal-squads/{squad_uuid}/bulk-actions/remove-many-users"
            resolved_ids = list(dict.fromkeys(value for value in numeric_ids if value is not None))
        elif any(value is not None for value in numeric_ids):
            logger.error("Cannot remove a mixed list of Remnawave 2.x and 3.x user identifiers.")
            return False
        else:
            results = await asyncio.gather(
                *(
                    self._update_legacy_user_squad_membership(squad_uuid, user_uuid, add=False)
                    for user_uuid in user_uuids
                )
            )
            if all(results):
                await self._invalidate_squad_caches()
                return True
            logger.error("Failed to remove one or more users from squad %s.", squad_uuid)
            return False
        for offset in range(0, len(resolved_ids), self._V3_SQUAD_BULK_LIMIT):
            response_data = await self._request(
                "DELETE",
                endpoint,
                json={"userIds": resolved_ids[offset : offset + self._V3_SQUAD_BULK_LIMIT]},
                log_full_response=False,
            )
            if not response_data or response_data.get("error"):
                logger.error(
                    "Failed to remove users from squad %s. Response: %s",
                    squad_uuid,
                    response_data,
                )
                return False
        await self._invalidate_squad_caches()
        for user_uuid in user_uuids:
            await self._invalidate_user_cache(user_uuid)
        await self._invalidate_all_users_cache()
        return True

    async def get_nodes_online_lookups(self) -> dict[str, dict[str, int]]:
        """Live ``usersOnline`` per node from ``GET /nodes`` (node directory).

        Newer panels expose Prometheus-style metrics under ``/system/stats/nodes``
        (``nodes: [{ usersOnline, ... }]``). Older/alternate builds only return
        historical rows (e.g. ``lastSevenDays``) without live counts. The node
        directory response always includes ``usersOnline`` and ``uuid``.

        Returns:
            ``{"byUuid": {uuid_lower: int}, "byName": {name_lower: int}}``
        """
        by_uuid: dict[str, int] = {}
        by_name: dict[str, int] = {}
        page_size = 100
        start = 0
        while True:
            response_data = await self._request(
                "GET",
                "/nodes",
                params={"size": page_size, "start": start},
                log_full_response=False,
            )
            if not response_data or response_data.get("error"):
                break
            resp = response_data.get("response")
            batch: list[dict[str, Any]] = []
            if isinstance(resp, list):
                batch = [x for x in resp if isinstance(x, dict)]
            elif isinstance(resp, dict):
                inner = resp.get("nodes") or resp.get("items") or []
                batch = [x for x in inner if isinstance(x, dict)]
            if not batch:
                break
            for n in batch:
                uid = n.get("uuid") or n.get("nodeUuid") or n.get("node_uuid")
                uo = n.get("usersOnline")
                if uo is None:
                    uo = n.get("users_online")
                if uo is None:
                    continue
                try:
                    val = int(uo)
                except (TypeError, ValueError):
                    continue
                if uid:
                    by_uuid[str(uid).strip().lower()] = val
                name = n.get("name")
                if name and isinstance(name, str) and name.strip():
                    by_name[name.strip().lower()] = val
            if len(batch) < page_size:
                break
            start += page_size
            await asyncio.sleep(0.05)
        return {"byUuid": by_uuid, "byName": by_name}

    async def get_nodes_statistics(self) -> dict[str, Any] | None:
        """Get nodes statistics"""
        response_data = await self._request("GET", "/system/stats/nodes", log_full_response=False)
        if response_data and not response_data.get("error") and "response" in response_data:
            return _json_dict(response_data.get("response"))
        return None

    async def encrypt_happ_link(self, link_to_encrypt: str) -> str | None:
        """Encrypt a subscription link locally with Happ Crypt4.

        Returns the encrypted link string or None if encryption failed.
        """
        return create_happ_crypt4_link(link_to_encrypt)
