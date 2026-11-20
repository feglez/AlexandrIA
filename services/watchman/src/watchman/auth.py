"""Authentication with Nextcloud credentials (HTTP Basic).

Members use their Nextcloud user name and an app password. The credentials
are checked with Nextcloud's OCS API and the result is cached in Redis for a
few minutes, keyed by a hash of the credentials.
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from redis.asyncio import Redis
from redis.exceptions import RedisError

from watchman.problems import Problem

logger = logging.getLogger(__name__)

_CHALLENGE = {"WWW-Authenticate": 'Basic realm="AlexandrIA"'}

_basic = HTTPBasic(auto_error=False)


@dataclass(frozen=True)
class User:
    id: str
    display_name: str
    groups: frozenset[str]

    def to_json(self) -> str:
        return json.dumps(
            {"id": self.id, "display_name": self.display_name, "groups": sorted(self.groups)}
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> "User":
        data = json.loads(raw)
        return cls(data["id"], data["display_name"], frozenset(data["groups"]))


class NextcloudAuthenticator:
    def __init__(self, nextcloud: httpx.AsyncClient, redis: Redis, cache_seconds: int) -> None:
        self._nextcloud = nextcloud
        self._redis = redis
        self._cache_seconds = cache_seconds

    async def authenticate(self, username: str, password: str) -> User:
        key = "auth:" + hashlib.sha256(f"{username}\0{password}".encode()).hexdigest()
        user = await self._cached(key)
        if user is not None:
            return user

        try:
            response = await self._nextcloud.get(
                "/ocs/v2.php/cloud/user",
                params={"format": "json"},
                auth=(username, password),
                headers={"OCS-APIRequest": "true"},
            )
        except httpx.HTTPError as exc:
            logger.warning("Nextcloud is not reachable: %s", exc)
            raise Problem(503, "Nextcloud is not reachable, try again later.") from exc

        if response.status_code == 401:
            raise Problem(401, "Invalid user name or password.", headers=_CHALLENGE)
        if response.status_code != 200:
            logger.warning("Unexpected answer from Nextcloud: HTTP %s", response.status_code)
            raise Problem(503, "Nextcloud could not check the credentials, try again later.")

        data = response.json()["ocs"]["data"]
        user = User(
            id=data["id"],
            display_name=data.get("displayname") or data["id"],
            groups=frozenset(data.get("groups") or []),
        )
        await self._store(key, user)
        return user

    async def _cached(self, key: str) -> User | None:
        # The cache is an optimisation: if Redis fails, ask Nextcloud.
        try:
            raw = await self._redis.get(key)
        except RedisError as exc:
            logger.warning("Auth cache not available: %s", exc)
            return None
        return User.from_json(raw) if raw else None

    async def _store(self, key: str, user: User) -> None:
        try:
            await self._redis.set(key, user.to_json(), ex=self._cache_seconds)
        except RedisError as exc:
            logger.warning("Auth cache not available: %s", exc)


async def current_user(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
) -> User:
    if credentials is None:
        raise Problem(401, "Authentication required.", headers=_CHALLENGE)
    authenticator: NextcloudAuthenticator = request.app.state.resources.authenticator
    return await authenticator.authenticate(credentials.username, credentials.password)


async def current_curator(request: Request, user: Annotated[User, Depends(current_user)]) -> User:
    if request.app.state.resources.settings.curators_group not in user.groups:
        raise Problem(403, "Only curators can do this.")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
CurrentCurator = Annotated[User, Depends(current_curator)]
