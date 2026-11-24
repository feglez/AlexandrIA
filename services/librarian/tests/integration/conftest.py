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
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote, unquote

import httpx
import pytest
import respx
from sqlalchemy import make_url, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from ulid import ULID

from librarian.app import create_public_app
from librarian.config import Settings
from librarian.consumer import EventConsumer
from librarian.db.migrate import upgrade_database
from librarian.db.models import Chunk, Document
from librarian.embeddings import Embedder
from librarian.indexer import Indexer
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
SERVICE = ("alexandria", "service-password")
GROUPS = {"alice": ["members"], "bob": ["members"], "carol": ["members", "curators"]}
PUBLIC_BASE_URL = "https://alexandria.test"
DAV = f"{NEXTCLOUD}/remote.php/dav/files/alexandria"

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
        nextcloud_service_password=SERVICE[1],
    )
    resources = Resources.from_settings(settings, embedder)
    async with resources.engine.begin() as connection:
        await connection.execute(text("TRUNCATE documents, chunks, reindex_jobs"))
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


# --- librarian only: the library in Nextcloud, events and the indexer ------


class FakeLibrary:
    """The Library folder of the service account, in the mocked Nextcloud."""

    def __init__(self) -> None:
        # path -> (file id, content)
        self.files: dict[str, tuple[int, bytes]] = {}
        self.collections = {"Manuals", "Invoices", "School"}
        self.downloads: list[str] = []
        # When set, Nextcloud answers every request with this status.
        self.status: int | None = None

    def add(self, path: str, content: bytes) -> int:
        file_id = next(_file_ids)
        self.files[path] = (file_id, content)
        return file_id

    def move(self, old: str, new: str) -> None:
        self.files[new] = self.files.pop(old)

    def get(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != basic_auth(*SERVICE):
            return httpx.Response(401)
        if self.status:
            return httpx.Response(self.status)
        path = unquote(request.url.path).removeprefix("/remote.php/dav/files/alexandria/")
        self.downloads.append(path)
        if path not in self.files:
            return httpx.Response(404)
        return httpx.Response(200, content=self.files[path][1])

    def propfind(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != basic_auth(*SERVICE):
            return httpx.Response(401)
        if self.status:
            return httpx.Response(self.status)
        assert request.headers["Depth"] == "infinity"
        folders = {"Library"} | {f"Library/{name}" for name in self.collections}
        for path in self.files:
            parts = path.split("/")
            folders |= {"/".join(parts[:end]) for end in range(1, len(parts))}
        entries = [(folder + "/", 100 + number, True) for number, folder in enumerate(folders)]
        entries += [(path, file_id, False) for path, (file_id, _) in self.files.items()]
        responses = "".join(
            f"<d:response><d:href>/remote.php/dav/files/alexandria/{quote(path)}</d:href>"
            f"<d:propstat><d:prop><oc:fileid>{file_id}</oc:fileid><d:resourcetype>"
            f"{'<d:collection/>' if folder else ''}</d:resourcetype></d:prop>"
            "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            for path, file_id, folder in entries
        )
        body = (
            '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" '
            f'xmlns:oc="http://owncloud.org/ns">{responses}</d:multistatus>'
        )
        return httpx.Response(207, text=body)


@pytest.fixture
def library(mocked: respx.MockRouter) -> FakeLibrary:
    fake = FakeLibrary()
    mocked.get(url__startswith=f"{DAV}/", name="download").mock(side_effect=fake.get)
    mocked.route(method="PROPFIND", url=f"{DAV}/Library", name="listing").mock(
        side_effect=fake.propfind
    )
    return fake


@pytest.fixture
def consumer(resources: Resources) -> EventConsumer:
    return EventConsumer(resources, name="test")


@pytest.fixture
def indexer(resources: Resources) -> Indexer:
    return Indexer(resources, retry_seconds=30, poll_seconds=0.05)


def published(file_id: int, path: str, content: bytes) -> dict[str, Any]:
    """The data of watchman's file.published for this file."""
    return {
        "submission_id": f"sub_{ULID()}",
        "path": path,
        "collection": path.split("/")[1],
        "file_id": file_id,
        "size_bytes": len(content),
        "mime_type": "text/markdown",
        "sha256": hashlib.sha256(content).hexdigest(),
        "submitted_by": "alice",
    }


async def emit(
    resources: Resources, event_type: str, data: dict[str, Any], *, actor: str = "watchman"
) -> str:
    """Add an event to the test stream, as watchman would."""
    event = {
        "id": str(ULID()),
        "type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "actor": actor,
        "data": data,
    }
    await resources.redis.xadd(resources.settings.events_stream, {"event": json.dumps(event)})
    return event["id"]


async def drain(consumer: EventConsumer) -> int:
    """Handle every entry waiting in the stream; returns how many there were."""
    await consumer.create_group()
    handled = 0
    while count := await consumer.consume():
        handled += count
    return handled


async def index_all(indexer: Indexer) -> int:
    """Make one attempt at every due document; returns how many there were."""
    attempts = 0
    while await indexer.index_next():
        attempts += 1
    return attempts


async def document_of(resources: Resources, file_id: int) -> Document | None:
    async with resources.sessions() as session:
        return await session.scalar(select(Document).where(Document.file_id == file_id))


async def passages_of(resources: Resources, document_id: str) -> list[str]:
    async with resources.sessions() as session:
        rows = await session.scalars(
            select(Chunk.text).where(Chunk.document_id == document_id).order_by(Chunk.ord)
        )
        return list(rows)


async def make_due(resources: Resources, file_id: int) -> None:
    """Move the next attempt at the document into the past."""
    async with resources.sessions.begin() as session:
        await session.execute(
            update(Document)
            .where(Document.file_id == file_id)
            .values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )
