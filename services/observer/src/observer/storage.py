"""Personal storage of the members, read from Nextcloud's OCS API.

`GET /ocs/v2.php/cloud/users/{user}` as the `observer` account, a sub-admin
of the members group, so it can read the members' details and nothing else.
The answer has `quota.used` (bytes in the member's files) and `quota.quota`
(the limit in bytes, -3 when the member has no limit). The client also takes
"none" for no limit, and sizes given as floats.

`GET /ocs/v2.php/cloud/groups/{group}/users` lists the members, which a
sub-admin of that group may read too.
"""

import logging
from dataclasses import dataclass
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# Nextcloud's value for "no limit" (FileInfo::SPACE_UNLIMITED).
UNLIMITED = -3


@dataclass(frozen=True)
class Storage:
    used_bytes: int
    # None when the member has no limit.
    quota_bytes: int | None


def _number(value: object) -> int | None:
    # PHP on a 32-bit system may give sizes over 2 GiB as floats.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


class NextcloudStorage:
    def __init__(self, client: httpx.AsyncClient, user: str, password: str) -> None:
        self._client = client
        self._auth = (user, password)

    async def usage(self, member: str) -> Storage | None:
        """The member's storage, or None if Nextcloud does not tell."""
        try:
            response = await self._client.get(
                f"/ocs/v2.php/cloud/users/{quote(member, safe='')}",
                params={"format": "json"},
                auth=self._auth,
                headers={"OCS-APIRequest": "true"},
            )
        except httpx.HTTPError as exc:
            logger.warning("Cannot read the storage of %s: %r", member, exc)
            return None
        if response.status_code != 200:
            logger.warning("Cannot read the storage of %s: HTTP %s", member, response.status_code)
            return None
        try:
            quota = response.json()["ocs"]["data"]["quota"]
            used, limit = quota["used"], quota["quota"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Cannot read the storage of %s: unexpected answer %r", member, exc)
            return None
        unlimited = limit in ("none", UNLIMITED)
        used, limit = _number(used), None if unlimited else _number(limit)
        if used is None or used < 0 or (not unlimited and (limit is None or limit < 0)):
            logger.warning("Cannot read the storage of %s: unexpected quota %r", member, quota)
            return None
        return Storage(used_bytes=used, quota_bytes=limit)

    async def members(self, group: str) -> list[str] | None:
        """The user names in the group, or None if Nextcloud does not tell."""
        try:
            response = await self._client.get(
                f"/ocs/v2.php/cloud/groups/{quote(group, safe='')}/users",
                params={"format": "json"},
                auth=self._auth,
                headers={"OCS-APIRequest": "true"},
            )
        except httpx.HTTPError as exc:
            logger.warning("Cannot list the members of %s: %r", group, exc)
            return None
        if response.status_code != 200:
            logger.warning("Cannot list the members of %s: HTTP %s", group, response.status_code)
            return None
        try:
            users = response.json()["ocs"]["data"]["users"]
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("Cannot list the members of %s: unexpected answer %r", group, exc)
            return None
        if not isinstance(users, list) or not all(isinstance(u, str) and u for u in users):
            logger.warning("Cannot list the members of %s: unexpected users %r", group, users)
            return None
        return users
