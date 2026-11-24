"""Integration tests use a real PostgreSQL and Redis.

Set TEST_DATABASE_URL (a superuser connection, any database) and
TEST_REDIS_URL. Each run creates a fresh database named test_librarian.
Without them the tests are skipped, except in CI, where they fail.
"""

import asyncio
import hashlib
import itertools
import json
import os
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from librarian.app import create_public_app
from librarian.config import Settings
from librarian.db.migrate import upgrade_database
from librarian.db.models import Chunk, Document
from librarian.embeddings import Embedder
from librarian.resources import Resources
from tests.conftest import NEXTCLOUD, basic_auth
from tests.embeddings import FakeEmbedder

TEST_DATABASE = "test_librarian"


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


# --- librarian only: an index of test documents, Nextcloud mocked ----------

ALICE = ("alice", "alice-app-password")
BOB = ("bob", "bob-app-password")
CAROL = ("carol", "carol-app-password")
GROUPS = {"alice": ["members"], "bob": ["members"], "carol": ["members", "curators"]}
PUBLIC_BASE_URL = "https://alexandria.test"

_file_ids = itertools.count(4182)


def nextcloud_user(request: httpx.Request) -> httpx.Response:
    for user, password in (ALICE, BOB, CAROL):
        if request.headers.get("Authorization") == basic_auth(user, password):
            data = {"id": user, "displayname": user.title(), "groups": GROUPS[user]}
            return httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": data}})
    return httpx.Response(401)


@pytest.fixture
def embedder() -> Embedder:
    """The fake one. The relevance tests override this fixture with the real model."""
    return FakeEmbedder()


@pytest.fixture
async def resources(
    migrated_database_url: str, redis_url: str, embedder: Embedder
) -> AsyncIterator[Resources]:
    settings = Settings(
        database_url=migrated_database_url,
        redis_url=redis_url,
        nextcloud_url=NEXTCLOUD,
        public_base_url=PUBLIC_BASE_URL,
        events_stream=f"test.events.{ULID()}",
    )
    resources = Resources.from_settings(settings, embedder)
    async with resources.engine.begin() as connection:
        await connection.execute(text("TRUNCATE documents, chunks"))
    yield resources
    await resources.redis.delete(settings.events_stream)
    await resources.aclose()


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
        yield router


async def index(
    resources: Resources, path: str, *passages: str, status: str = "indexed"
) -> Document:
    """Store a document of the library with its passages, embedded by the
    resources' embedder. The collection is the folder after "Library/"."""
    embeddings = await resources.embedder.passages(passages)
    document = Document(
        id=f"doc_{ULID()}",
        file_id=next(_file_ids),
        path=path,
        collection=path.split("/")[1],
        sha256=hashlib.sha256("\n".join(passages).encode()).hexdigest(),
        status=status,
        chunks=len(passages),
        indexed_at=datetime.now(UTC) if status == "indexed" else None,
    )
    async with resources.sessions.begin() as session:
        session.add(document)
        await session.flush()
        session.add_all(
            Chunk(document_id=document.id, ord=number, text=passage, embedding=embedding)
            for number, (passage, embedding) in enumerate(zip(passages, embeddings, strict=True))
        )
    return document


async def events(resources: Resources) -> list[dict[str, Any]]:
    entries = await resources.redis.xrange(resources.settings.events_stream)
    return [json.loads(fields[b"event"]) for _, fields in entries]
