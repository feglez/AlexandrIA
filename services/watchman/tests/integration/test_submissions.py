"""POST /api/v1/submissions and GET /api/v1/collections with a real PostgreSQL
and Redis. Nextcloud and observer are mocked with respx."""

import hashlib
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import func, select, text
from ulid import ULID

from tests import samples
from tests.conftest import NEXTCLOUD, basic_auth
from tests.unit.test_files import multistatus
from watchman.app import create_public_app
from watchman.config import Settings
from watchman.db.models import LibraryFile, StatusChange, Submission
from watchman.policy import Policy, active_policy
from watchman.resources import Resources

OBSERVER = "http://observer.test:8001"
DAV = f"{NEXTCLOUD}/remote.php/dav/files/alexandria"
DAV_PATH = "/remote.php/dav/files/alexandria"
ALICE = ("alice", "alice-app-password")
ALLOWED = {"allowed": True, "remaining_bytes": 4_079_736_799, "remaining_submissions": 35}


def nextcloud_user(request: httpx.Request) -> httpx.Response:
    if request.headers.get("Authorization") != basic_auth(*ALICE):
        return httpx.Response(401)
    data = {"id": "alice", "displayname": "Alice", "groups": ["members"]}
    return httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": data}})


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


async def stored(resources: Resources) -> list[Submission]:
    async with resources.sessions() as session:
        return list(await session.scalars(select(Submission)))


async def events(resources: Resources) -> list[dict]:
    entries = await resources.redis.xrange(resources.settings.events_stream)
    return [json.loads(fields[b"event"]) for _, fields in entries]


async def test_accepted_submission(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    response = await submit(client, note="Warranty for the new washing machine")

    assert response.status_code == 202
    body = response.json()
    assert body["id"].startswith("sub_")
    assert body["status"] == "pending_review"
    assert body["filename"] == "warranty.pdf"
    assert body["collection"] == "Manuals"
    assert body["size_bytes"] == len(samples.PDF)
    assert body["mime_type"] == "application/pdf"
    assert body["sha256"] == hashlib.sha256(samples.PDF).hexdigest()
    assert body["submitted_by"] == "alice"
    assert body["note"] == "Warranty for the new washing machine"
    assert body["created_at"].endswith("Z")
    assert [(c["name"], c["result"]) for c in body["checks"]] == [
        ("size_limit", "pass"),
        ("mime_consistency", "pass"),
        ("blocked_type", "pass"),
        ("duplicate", "pass"),
        ("quota", "pass"),
    ]

    # In the quarantine folder of the service account, not in the library.
    assert mocked["mkcol"].calls.last.request.url.path == f"{DAV_PATH}/Quarantine/{body['id']}"
    put = mocked["put"].calls.last.request
    assert put.url.path == f"{DAV_PATH}/Quarantine/{body['id']}/warranty.pdf"
    assert put.headers["Authorization"] == basic_auth("alexandria", "service-password")
    assert put.content == samples.PDF
    quota_request = json.loads(mocked["quota"].calls.last.request.content)
    assert quota_request == {"user": "alice", "size_bytes": len(samples.PDF)}

    [submission] = await stored(resources)
    assert submission.status == "pending_review"
    assert submission.quarantine_path == f"Quarantine/{body['id']}/warranty.pdf"
    async with resources.sessions() as session:
        [change] = await session.scalars(select(StatusChange))
    assert (change.from_status, change.to_status, change.actor) == (
        "checking",
        "pending_review",
        "alice",
    )

    [event] = await events(resources)
    assert event["type"] == "submission.created"
    assert event["actor"] == "alice"
    assert event["data"]["submission_id"] == body["id"]
    assert event["data"]["status"] == "pending_review"
    assert event["data"]["size_bytes"] == len(samples.PDF)


async def test_docx_and_text_files(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    for content, filename in [(samples.DOCX, "letter.docx"), (samples.MARKDOWN, "notes.md")]:
        response = await submit(client, content=content, filename=filename)
        assert response.status_code == 202, response.json()


@pytest.mark.parametrize("auth", [None, ("alice", "wrong-password")], ids=["none", "wrong"])
async def test_invalid_credentials(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    auth: tuple[str, str] | None,
) -> None:
    response = await submit(client, auth=auth)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")
    assert not mocked["collections"].called
    assert not mocked["put"].called
    assert await stored(resources) == []


async def rejected(
    response: httpx.Response, resources: Resources, mocked: respx.MockRouter, reason: str
) -> dict:
    """Common assertions for a submission rejected by a check."""
    assert response.status_code == 422
    assert response.headers["Content-Type"] == "application/problem+json"
    body = response.json()
    assert body["reason"] == reason
    assert body["submission_id"].startswith("sub_")
    assert not mocked["put"].called

    [submission] = await stored(resources)
    assert submission.id == body["submission_id"]
    assert submission.status == "rejected"
    assert submission.quarantine_path is None
    [event] = await events(resources)
    assert event["data"]["status"] == "rejected"
    return body


async def test_file_over_the_size_limit(
    app: FastAPI, client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    app.dependency_overrides[active_policy] = lambda: Policy(max_size_bytes=50)

    body = await rejected(await submit(client), resources, mocked, "file_too_large")

    assert body["type"] == "https://alexandria.test/problems/file-too-large"
    assert body["detail"] == f"The file is {len(samples.PDF)} bytes, over the limit of 50 bytes."
    checks = {check["name"]: check["result"] for check in body["checks"]}
    assert checks["size_limit"] == "fail"
    assert checks["quota"] == "skipped"
    assert not mocked["quota"].called


async def test_request_far_over_the_size_limit(
    app: FastAPI, client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    app.dependency_overrides[active_policy] = lambda: Policy(max_size_bytes=50)

    response = await submit(client, content=samples.PDF * 2000)

    assert response.status_code == 413
    assert response.json()["reason"] == "file_too_large"
    assert not mocked["collections"].called
    assert await stored(resources) == []


async def test_content_does_not_match_the_extension(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    response = await submit(client, content=samples.TEXT, filename="warranty.pdf")

    body = await rejected(response, resources, mocked, "mime_mismatch")
    assert body["type"] == "https://alexandria.test/problems/mime-mismatch"


async def test_blocked_type(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    response = await submit(client, content=samples.EXE, filename="setup.exe")

    await rejected(response, resources, mocked, "blocked_type")


async def test_duplicate_of_a_library_file(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    async with resources.sessions() as session:
        session.add(
            LibraryFile(
                file_id=4182,
                path="Library/Manuals/washing-machine-warranty.pdf",
                collection="Manuals",
                sha256=hashlib.sha256(samples.PDF).hexdigest(),
                size_bytes=len(samples.PDF),
                submitted_by="bob",
            )
        )
        await session.commit()

    body = await rejected(await submit(client), resources, mocked, "duplicate")

    assert body["type"] == "https://alexandria.test/problems/duplicate-file"
    assert body["title"] == "Duplicate file"
    assert body["existing_path"] == "Library/Manuals/washing-machine-warranty.pdf"


async def test_quota_exceeded(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    mocked["quota"].respond(json=ALLOWED | {"allowed": False, "remaining_bytes": 10})

    body = await rejected(await submit(client), resources, mocked, "quota_exceeded")

    assert body["type"] == "https://alexandria.test/problems/quota-exceeded"


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.Response(404)],
    ids=["down", "timeout", "no-route"],
)
async def test_observer_unavailable_means_pending_review(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    failure: Exception | httpx.Response,
) -> None:
    if isinstance(failure, Exception):
        mocked["quota"].mock(side_effect=failure)
    else:
        mocked["quota"].mock(return_value=failure)

    response = await submit(client)

    assert response.status_code == 202
    assert response.json()["status"] == "pending_review"
    assert response.json()["checks"][-1]["result"] == "unavailable"
    assert mocked["put"].called


async def test_path_in_the_file_name_is_dropped(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    response = await submit(client, filename="../../Library/Manuals/warranty.pdf")

    assert response.status_code == 202
    submission_id = response.json()["id"]
    put = mocked["put"].calls.last.request
    assert put.url.path == f"{DAV_PATH}/Quarantine/{submission_id}/warranty.pdf"


@pytest.mark.parametrize(
    ("fields", "location"),
    [
        ({"collection": "Recipes"}, "collection"),
        ({"collection": ""}, "collection"),
        ({"filename": "..", "collection": "Manuals"}, "file"),
        ({"filename": "bad‮gpj.pdf", "collection": "Manuals"}, "file"),
        ({"content": b"", "collection": "Manuals"}, "file"),
    ],
    ids=["unknown-collection", "no-collection", "dot-dot", "control-character", "empty"],
)
async def test_invalid_form(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    fields: dict,
    location: str,
) -> None:
    response = await submit(client, **fields)

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "https://alexandria.test/problems/validation-error"
    assert body["errors"][0]["loc"] == ["body", location]
    assert await stored(resources) == []


async def test_missing_file(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    response = await client.post("/api/v1/submissions", data={"collection": "Manuals"}, auth=ALICE)

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"] == ["body", "file"]


async def test_upload_failure_leaves_nothing_behind(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    mocked["put"].respond(507)

    response = await submit(client)

    assert response.status_code == 503
    assert await stored(resources) == []
    assert await events(resources) == []
    assert mocked["delete"].called


async def test_each_submission_gets_its_own_folder(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    first = await submit(client, content=samples.TEXT, filename="notes.txt")
    second = await submit(client, content=samples.TEXT, filename="notes.txt")

    assert first.status_code == second.status_code == 202
    async with resources.sessions() as session:
        paths = set(await session.scalars(select(Submission.quarantine_path)))
        changes = await session.scalar(select(func.count()).select_from(StatusChange))
    assert len(paths) == 2
    assert changes == 2


async def test_list_collections(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    response = await client.get("/api/v1/collections", auth=ALICE)

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"name": "Manuals", "path": "Library/Manuals"},
            {"name": "Taxes", "path": "Library/Taxes"},
        ],
        "total": 2,
    }


async def test_collections_need_credentials(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await client.get("/api/v1/collections")

    assert response.status_code == 401


async def test_collections_when_nextcloud_is_down(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    mocked["collections"].mock(side_effect=httpx.ConnectError("refused"))

    response = await client.get("/api/v1/collections", auth=ALICE)

    assert response.status_code == 503
