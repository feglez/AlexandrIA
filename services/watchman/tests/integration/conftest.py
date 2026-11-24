"""Integration tests use a real PostgreSQL and Redis.

Set TEST_DATABASE_URL (a superuser connection, any database) and
TEST_REDIS_URL. Each run creates a fresh database named test_watchman.
Without them the tests are skipped, except in CI, where they fail.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import make_url, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from tests import samples
from tests.conftest import NEXTCLOUD, basic_auth
from tests.unit.test_files import file_id_answer, multistatus
from watchman.app import create_public_app
from watchman.config import Settings
from watchman.db.migrate import upgrade_database
from watchman.db.models import Submission
from watchman.policy import DEFAULT_POLICY, Policy, save
from watchman.resources import Resources

TEST_DATABASE = "test_watchman"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        if os.environ.get("CI"):
            pytest.fail(f"{name} must be set in CI")
        pytest.skip(f"{name} is not set")
    return value


async def _recreate_database(admin_url: str) -> None:
    engine = create_async_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)'))
        await connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    admin_url = _required("TEST_DATABASE_URL")
    asyncio.run(_recreate_database(admin_url))
    url = make_url(admin_url).set(database=TEST_DATABASE)
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def redis_url() -> str:
    return _required("TEST_REDIS_URL")


@pytest.fixture(scope="session")
def migrated_database_url(database_url: str) -> str:
    upgrade_database(database_url)
    return database_url


# --- watchman only: the public app with Nextcloud and observer mocked -------

OBSERVER = "http://observer.test:8001"
DAV = f"{NEXTCLOUD}/remote.php/dav/files/alexandria"
DAV_PATH = "/remote.php/dav/files/alexandria"
ALICE = ("alice", "alice-app-password")
BOB = ("bob", "bob-app-password")
CAROL = ("carol", "carol-app-password")
DAVE = ("dave", "dave-app-password")
GROUPS = {
    "alice": ["members"],
    "bob": ["members"],
    "carol": ["members", "curators"],
    "dave": ["members", "trusted"],
}
ALLOWED = {"allowed": True, "remaining_bytes": 4_079_736_799, "remaining_submissions": 35}
# Nextcloud file id of every quarantined file in the mocks.
FILE_ID = 4182
NOTIFICATIONS = f"{NEXTCLOUD}/ocs/v2.php/apps/notifications/api/v3/admin_notifications/"


def nextcloud_user(request: httpx.Request) -> httpx.Response:
    for user, password in (ALICE, BOB, CAROL, DAVE):
        if request.headers.get("Authorization") == basic_auth(user, password):
            data = {"id": user, "displayname": user.title(), "groups": GROUPS[user]}
            return httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": data}})
    return httpx.Response(401)


@pytest.fixture
async def resources(migrated_database_url: str, redis_url: str) -> AsyncIterator[Resources]:
    settings = Settings(
        database_url=migrated_database_url,
        redis_url=redis_url,
        nextcloud_url=NEXTCLOUD,
        public_base_url="https://alexandria.test",
        nextcloud_service_password="service-password",
        observer_internal_url=OBSERVER,
        observer_internal_token="observer-token",
        events_stream=f"test.events.{ULID()}",
    )
    resources = Resources.from_settings(settings)
    async with resources.engine.begin() as connection:
        await connection.execute(text("TRUNCATE submissions, status_history, library_files"))
        # Keep only the policy stored by the migration.
        await connection.execute(text("DELETE FROM policies WHERE version > 1"))
    yield resources
    await resources.redis.delete(settings.events_stream)
    await resources.aclose()


@pytest.fixture
def app(resources: Resources) -> FastAPI:
    return create_public_app(resources)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def mocked() -> respx.MockRouter:
    library = f"{DAV_PATH}/Library"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{NEXTCLOUD}/ocs/v2.php/cloud/user", name="auth").mock(
            side_effect=nextcloud_user
        )
        router.route(method="PROPFIND", url=f"{DAV}/Library", name="collections").respond(
            207, text=multistatus(f"{library}/", f"{library}/Manuals/", f"{library}/Taxes/")
        )
        router.route(
            method="MKCOL", url__regex=rf"{DAV}/Quarantine/sub_\w+$", name="mkcol"
        ).respond(201)
        router.put(url__startswith=f"{DAV}/Quarantine/", name="put").respond(201)
        router.delete(url__startswith=f"{DAV}/Quarantine/", name="delete").respond(204)
        router.post(f"{OBSERVER}/internal/quota-checks", name="quota").respond(json=ALLOWED)
        # File ids: every quarantined file exists, the library is empty.
        router.route(
            method="PROPFIND", url__startswith=f"{DAV}/Quarantine/", name="source_id"
        ).respond(207, text=file_id_answer(f"{DAV_PATH}/Quarantine/file", FILE_ID))
        router.route(
            method="PROPFIND", url__startswith=f"{DAV}/Library/", name="target_id"
        ).respond(404)
        router.route(method="MOVE", url__startswith=f"{DAV}/Quarantine/", name="move").respond(201)
        router.post(url__startswith=NOTIFICATIONS, name="notify").respond(
            json={"ocs": {"meta": {"statuscode": 200}, "data": {"id": 1}}}
        )
        yield router


async def submit(
    client: httpx.AsyncClient,
    content: bytes = samples.PDF,
    filename: str = "warranty.pdf",
    collection: str = "Manuals",
    auth: tuple[str, str] | None = ALICE,
    **data: str,
) -> httpx.Response:
    return await client.post(
        "/api/v1/submissions",
        files={"file": (filename, content, "application/octet-stream")},
        data={"collection": collection, **data},
        auth=auth,
    )


async def use_policy(resources: Resources, **changes: object) -> int:
    """Store a new active policy: the default one with some fields replaced."""
    policy = Policy.model_validate(DEFAULT_POLICY.model_dump() | changes)
    async with resources.sessions() as session:
        return (await save(session, policy, "carol")).version


async def stored(resources: Resources) -> list[Submission]:
    async with resources.sessions() as session:
        return list(await session.scalars(select(Submission)))


async def events(resources: Resources) -> list[dict]:
    entries = await resources.redis.xrange(resources.settings.events_stream)
    return [json.loads(fields[b"event"]) for _, fields in entries]
