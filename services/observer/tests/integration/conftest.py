"""Integration tests use a real PostgreSQL and Redis.

Set TEST_DATABASE_URL (a superuser connection, any database) and
TEST_REDIS_URL. Each run creates a fresh database named test_observer.
Without them the tests are skipped, except in CI, where they fail.
"""

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import make_url, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from observer.app import create_public_app
from observer.config import Settings
from observer.consumer import EventConsumer
from observer.db.migrate import upgrade_database
from observer.db.models import UsageTotal
from observer.resources import Resources
from tests.conftest import NEXTCLOUD, basic_auth

TEST_DATABASE = "test_observer"


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


# --- observer only: events on a stream of its own, Nextcloud mocked ---------

ALICE = ("alice", "alice-app-password")
BOB = ("bob", "bob-app-password")
CAROL = ("carol", "carol-app-password")
GROUPS = {"alice": ["members"], "bob": ["members"], "carol": ["members", "curators"]}
OBSERVER_ACCOUNT = ("observer", "observer-password")
USERS = f"{NEXTCLOUD}/ocs/v2.php/cloud/users/"
# Personal storage of each member, as Nextcloud reports it (-3: no quota).
STORAGE = {
    "alice": {"free": -3, "used": 8_321_499_136, "total": -3, "relative": 0, "quota": -3},
    "bob": {
        "free": 1_073_741_301,
        "used": 523,
        "total": 1_073_741_824,
        "relative": 0,
        "quota": 1_073_741_824,
    },
}


def nextcloud_user(request: httpx.Request) -> httpx.Response:
    for user, password in (ALICE, BOB, CAROL):
        if request.headers.get("Authorization") == basic_auth(user, password):
            data = {"id": user, "displayname": user.title(), "groups": GROUPS[user]}
            return httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": data}})
    return httpx.Response(401)


def nextcloud_user_details(request: httpx.Request) -> httpx.Response:
    if request.headers.get("Authorization") != basic_auth(*OBSERVER_ACCOUNT):
        return httpx.Response(401)
    user = request.url.path.rsplit("/", 1)[1]
    if user not in STORAGE:
        return httpx.Response(404, json={"ocs": {"meta": {"statuscode": 404}, "data": []}})
    data = {"id": user, "quota": STORAGE[user]}
    return httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": data}})


@pytest.fixture
async def resources(migrated_database_url: str, redis_url: str) -> AsyncIterator[Resources]:
    settings = Settings(
        database_url=migrated_database_url,
        redis_url=redis_url,
        nextcloud_url=NEXTCLOUD,
        public_base_url="https://alexandria.test",
        nextcloud_observer_password=OBSERVER_ACCOUNT[1],
        events_stream=f"test.events.{ULID()}",
    )
    resources = Resources.from_settings(settings)
    async with resources.engine.begin() as connection:
        await connection.execute(text("TRUNCATE usage_events, usage_totals"))
    yield resources
    await resources.redis.delete(settings.events_stream)
    await resources.aclose()


@pytest.fixture
def consumer(resources: Resources) -> EventConsumer:
    return EventConsumer(resources, name="test-consumer")


@pytest.fixture
async def client(resources: Resources) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_public_app(resources))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def mocked() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{NEXTCLOUD}/ocs/v2.php/cloud/user", name="auth").mock(
            side_effect=nextcloud_user
        )
        router.get(url__startswith=USERS, name="storage").mock(side_effect=nextcloud_user_details)
        yield router


async def emit(
    resources: Resources,
    event_type: str,
    data: dict[str, Any],
    *,
    actor: str = "watchman",
    occurred_at: datetime | None = None,
    event_id: str | None = None,
) -> str:
    """Add an event to the test stream, as watchman or librarian would."""
    event = {
        "id": event_id or str(ULID()),
        "type": event_type,
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "actor": actor,
        "data": data,
    }
    await resources.redis.xadd(resources.settings.events_stream, {"event": json.dumps(event)})
    return event["id"]


def published(member: str, size_bytes: int, **changes: Any) -> dict[str, Any]:
    """Data of a file.published event."""
    return {
        "submission_id": f"sub_{ULID()}",
        "path": f"Library/Manuals/{ULID()}.pdf",
        "collection": "Manuals",
        "file_id": 4182,
        "size_bytes": size_bytes,
        "mime_type": "application/pdf",
        "sha256": "9f2c1e",
        "submitted_by": member,
    } | changes


def created(member: str, status: str = "pending_review") -> dict[str, Any]:
    """Data of a submission.created event."""
    return {
        "submission_id": f"sub_{ULID()}",
        "status": status,
        "filename": "warranty.pdf",
        "collection": "Manuals",
        "size_bytes": 482_133,
        "mime_type": "application/pdf",
        "sha256": "9f2c1e",
        "submitted_by": member,
    }


async def drain(consumer: EventConsumer) -> int:
    """Handle every entry waiting in the stream; returns how many there were."""
    await consumer.create_group()
    handled = 0
    while count := await consumer.consume():
        handled += count
    return handled


async def totals(resources: Resources) -> dict[tuple[str, str], tuple[int, int, int, int]]:
    """(member, period) -> (contributed_bytes, submissions, searches, questions)."""
    async with resources.sessions() as session:
        rows = await session.scalars(select(UsageTotal))
        return {
            (row.member, row.period): (
                row.contributed_bytes,
                row.submissions,
                row.searches,
                row.questions,
            )
            for row in rows
        }
