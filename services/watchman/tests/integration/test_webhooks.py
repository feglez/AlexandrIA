"""POST /internal/webhooks/nextcloud: files of the library moved or deleted in
Nextcloud. The payloads are the ones Nextcloud 35's webhook_listeners app
sends (recorded), which come without a Content-Type header."""

import json
from typing import Any

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select

from tests.integration.conftest import events
from watchman.api.webhooks import DELETED, RENAMED
from watchman.db.models import LibraryFile
from watchman.resources import Resources

FILES = "/alexandria/files"


def deleted(path: str, user: str = "alexandria") -> dict[str, Any]:
    return {
        "event": {"node": {"path": f"{FILES}/{path}"}, "class": DELETED},
        "user": {"uid": user, "displayName": user.title()},
        "time": 1795558488,
        "authentication": [],
    }


def renamed(source: str, target: str, file_id: int = 171, kind: str = "file") -> dict[str, Any]:
    return {
        "event": {
            "source": {"path": f"{FILES}/{source}"},
            "target": {
                "id": file_id,
                "path": f"{FILES}/{target}",
                "type": kind,
                "mimetype": "text/markdown" if kind == "file" else "httpd/unix-directory",
                "size": 6,
                "mtime": 1795558486,
            },
            "class": RENAMED,
        },
        "user": {"uid": "carol", "displayName": "Carol"},
        "time": 1795558487,
        "authentication": [],
    }


async def call(internal: httpx.AsyncClient, payload: dict[str, Any]) -> httpx.Response:
    return await internal.post("/internal/webhooks/nextcloud", content=json.dumps(payload))


async def publish(resources: Resources, path: str, file_id: int, size: int = 1000) -> None:
    async with resources.sessions.begin() as session:
        session.add(
            LibraryFile(
                file_id=file_id,
                path=path,
                collection=path.split("/")[1],
                sha256=f"{file_id:064x}",
                size_bytes=size,
                submitted_by="alice",
            )
        )


async def library(resources: Resources) -> dict[int, tuple[str, str]]:
    async with resources.sessions() as session:
        rows = await session.scalars(select(LibraryFile))
        return {row.file_id: (row.path, row.collection) for row in rows}


async def test_a_deleted_file_is_forgotten_and_removed_from_the_index(
    internal: httpx.AsyncClient, resources: Resources
) -> None:
    await publish(resources, "Library/Manuals/washer.md", 171, size=482133)
    await publish(resources, "Library/Manuals/other.md", 172)

    response = await call(internal, deleted("Library/Manuals/washer.md", user="admin"))

    assert response.status_code == 204
    assert await library(resources) == {172: ("Library/Manuals/other.md", "Manuals")}
    [event] = await events(resources)
    assert (event["type"], event["actor"]) == ("file.removed", "admin")
    assert event["data"] == {
        "file_id": 171,
        "path": "Library/Manuals/washer.md",
        "collection": "Manuals",
        "size_bytes": 482133,
        "submitted_by": "alice",
        "submission_id": None,
    }


async def test_a_deleted_folder_removes_every_file_inside_it(
    internal: httpx.AsyncClient, resources: Resources
) -> None:
    await publish(resources, "Library/Manuals/Old/a.md", 171)
    await publish(resources, "Library/Manuals/Old/2020/b.md", 172)
    await publish(resources, "Library/Manuals/Older.md", 173)

    await call(internal, deleted("Library/Manuals/Old"))

    assert list(await library(resources)) == [173]
    assert [event["data"]["file_id"] for event in await events(resources)] == [172, 171]


async def test_a_file_moved_within_the_library_gets_its_new_path(
    internal: httpx.AsyncClient, resources: Resources
) -> None:
    await publish(resources, "Library/Manuals/washer.md", 171)

    response = await call(
        internal, renamed("Library/Manuals/washer.md", "Library/Invoices/washer-2026.md")
    )

    assert response.status_code == 204
    assert await library(resources) == {171: ("Library/Invoices/washer-2026.md", "Invoices")}
    [event] = await events(resources)
    assert (event["type"], event["actor"]) == ("file.renamed", "carol")
    assert event["data"] == {
        "file_id": 171,
        "path": "Library/Invoices/washer-2026.md",
        "old_path": "Library/Manuals/washer.md",
        "collection": "Invoices",
        "size_bytes": 1000,
        "submitted_by": "alice",
        "submission_id": None,
    }


async def test_a_moved_folder_moves_every_file_inside_it(
    internal: httpx.AsyncClient, resources: Resources
) -> None:
    await publish(resources, "Library/Manuals/Old/a.md", 171)
    await publish(resources, "Library/Manuals/Old/2020/b.md", 172)

    await call(internal, renamed("Library/Manuals/Old", "Library/School/Older", 180, "dir"))

    assert await library(resources) == {
        171: ("Library/School/Older/a.md", "School"),
        172: ("Library/School/Older/2020/b.md", "School"),
    }
    assert [event["type"] for event in await events(resources)] == ["file.renamed"] * 2


@pytest.mark.parametrize(
    "target", ["Quarantine/washer.md", "Library/washer.md", "Elsewhere/Library/washer.md"]
)
async def test_a_file_moved_out_of_the_collections_is_removed(
    internal: httpx.AsyncClient, resources: Resources, target: str
) -> None:
    await publish(resources, "Library/Manuals/washer.md", 171)

    await call(internal, renamed("Library/Manuals/washer.md", target))

    assert await library(resources) == {}
    [event] = await events(resources)
    assert event["type"] == "file.removed"
    assert event["data"]["path"] == "Library/Manuals/washer.md"


async def test_a_file_overwritten_by_a_move_is_removed(
    internal: httpx.AsyncClient, resources: Resources
) -> None:
    await publish(resources, "Library/Manuals/washer.md", 171)
    await publish(resources, "Library/Manuals/washer (2).md", 172)

    await call(internal, renamed("Library/Manuals/washer (2).md", "Library/Manuals/washer.md", 172))

    assert await library(resources) == {172: ("Library/Manuals/washer.md", "Manuals")}
    assert [(event["type"], event["data"]["file_id"]) for event in await events(resources)] == [
        ("file.removed", 171),
        ("file.renamed", 172),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        # The mover publishing a file, and removing its quarantine folder.
        renamed("Quarantine/sub_01JB/washer.md", "Library/Manuals/washer.md"),
        deleted("Quarantine/sub_01JB"),
        # Files of the library that watchman did not publish.
        deleted("Library/Manuals/unknown.md"),
        renamed("Library/Manuals/unknown.md", "Library/Invoices/unknown.md"),
        # Another account's files.
        deleted("Library/Manuals/washer.md")
        | {
            "event": {
                "node": {"path": "/alice/files/Library/Manuals/washer.md"},
                "class": DELETED,
            }
        },
        # Other events.
        {"event": {"class": "OCP\\Files\\Events\\Node\\NodeCreatedEvent"}},
    ],
    ids=["publish", "quarantine", "unknown", "unknown moved", "other account", "other event"],
)
async def test_other_changes_are_ignored(
    internal: httpx.AsyncClient, resources: Resources, payload: dict[str, Any]
) -> None:
    await publish(resources, "Library/Manuals/washer.md", 171)

    response = await call(internal, payload)

    assert response.status_code == 204
    assert await library(resources) == {171: ("Library/Manuals/washer.md", "Manuals")}
    assert await events(resources) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"event": {"class": DELETED}},
        {"event": {"class": RENAMED, "source": {"path": f"{FILES}/Library/a.md"}}},
        {"user": {"uid": "carol"}},
        "not json",
    ],
    ids=["no node", "no target", "no event", "not json"],
)
async def test_an_invalid_call_is_refused(
    internal: httpx.AsyncClient, payload: dict[str, Any] | str
) -> None:
    content = payload if isinstance(payload, str) else json.dumps(payload)

    response = await internal.post("/internal/webhooks/nextcloud", content=content)

    assert response.status_code == 422


@pytest.mark.parametrize("secret", [None, "wrong-secret"])
async def test_a_call_without_the_secret_is_refused(
    resources: Resources, internal: httpx.AsyncClient, secret: str | None
) -> None:
    await publish(resources, "Library/Manuals/washer.md", 171)
    headers = {"Authorization": f"Bearer {secret}"} if secret else {}
    internal.headers.pop("Authorization")

    response = await internal.post(
        "/internal/webhooks/nextcloud",
        content=json.dumps(deleted("Library/Manuals/washer.md")),
        headers=headers,
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert await library(resources) != {}


async def test_the_webhook_is_not_on_the_public_app(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/internal/webhooks/nextcloud", content=json.dumps(deleted("Library/Manuals/a.md"))
    )

    assert response.status_code == 404


async def test_the_change_stays_when_the_event_cannot_be_published(
    internal: httpx.AsyncClient, resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    await publish(resources, "Library/Manuals/washer.md", 171)

    async def fail(*args: object) -> str:
        raise RedisConnectionError("Redis is down")

    monkeypatch.setattr(resources.events, "publish", fail)

    response = await call(internal, deleted("Library/Manuals/washer.md"))

    assert response.status_code == 204
    assert await library(resources) == {}
