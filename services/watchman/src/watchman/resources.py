"""Clients shared by the public and the internal app (one set per process)."""

import asyncio
from dataclasses import dataclass, field
from http.cookiejar import CookieJar, DefaultCookiePolicy

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from watchman.auth import NextcloudAuthenticator
from watchman.config import Settings
from watchman.events import EventPublisher
from watchman.files import NextcloudFiles
from watchman.notifications import Notifier
from watchman.quota import QuotaClient


def nextcloud_client(settings: Settings) -> httpx.AsyncClient:
    """HTTP client for Nextcloud that never keeps cookies.

    The same client sends the credentials of many users. Nextcloud answers
    with session cookies, and a request that carried them back would be
    authenticated as that session's user, whatever credentials it sent.
    """
    no_cookies = CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))
    return httpx.AsyncClient(base_url=settings.nextcloud_url, timeout=10.0, cookies=no_cookies)


@dataclass
class Resources:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    redis: Redis
    nextcloud: httpx.AsyncClient
    authenticator: NextcloudAuthenticator
    files: NextcloudFiles
    notifier: Notifier
    observer: httpx.AsyncClient
    quota: QuotaClient
    events: EventPublisher
    # Set after an approval, so the mover does not wait for its next poll.
    mover_wakeup: asyncio.Event = field(default_factory=asyncio.Event)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Resources":
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        redis = Redis.from_url(settings.redis_url)
        nextcloud = nextcloud_client(settings)
        observer = httpx.AsyncClient(
            base_url=settings.observer_internal_url, timeout=settings.quota_check_timeout_seconds
        )
        return cls(
            settings=settings,
            engine=engine,
            sessions=async_sessionmaker(engine, expire_on_commit=False),
            redis=redis,
            nextcloud=nextcloud,
            authenticator=NextcloudAuthenticator(nextcloud, redis, settings.auth_cache_seconds),
            files=NextcloudFiles(
                nextcloud, settings.nextcloud_service_user, settings.nextcloud_service_password
            ),
            notifier=Notifier(
                nextcloud, settings.nextcloud_service_user, settings.nextcloud_service_password
            ),
            observer=observer,
            quota=QuotaClient(observer, settings.observer_internal_token),
            events=EventPublisher(redis, settings.events_stream),
        )

    async def aclose(self) -> None:
        await self.observer.aclose()
        await self.nextcloud.aclose()
        await self.redis.aclose()
        await self.engine.dispose()
