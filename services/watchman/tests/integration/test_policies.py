"""GET and PUT /api/v1/policies with a real PostgreSQL and Redis. Nextcloud and
observer are mocked with respx."""

import asyncio
import copy
from datetime import datetime

import httpx
import pytest
import respx
from sqlalchemy import select

from tests import samples
from tests.integration.conftest import ALICE, CAROL, stored, submit
from watchman.db.models import PolicyVersion
from watchman.policy import DEFAULT_POLICY
from watchman.resources import Resources

DEFAULT = DEFAULT_POLICY.model_dump(mode="json")


def changed(**changes: object) -> dict:
    return copy.deepcopy(DEFAULT) | changes


async def put(client: httpx.AsyncClient, document: object, auth=CAROL) -> httpx.Response:
    return await client.put("/api/v1/policies", json=document, auth=auth)


async def versions(resources: Resources) -> list[PolicyVersion]:
    async with resources.sessions() as session:
        return list(await session.scalars(select(PolicyVersion).order_by(PolicyVersion.version)))


async def test_the_migration_stores_the_default_policy(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await client.get("/api/v1/policies", auth=CAROL)

    assert response.status_code == 200
    body = response.json()
    assert (body["version"], body["created_by"]) == (1, "watchman")
    assert body["policy"] == DEFAULT


async def test_curator_replaces_the_policy(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    document = changed(
        max_size_bytes=20_000_000,
        allowed_mime_types=["application/pdf", "Image/*"],
        auto_approve={"groups": ["trusted", "parents"], "max_size_bytes": 5_000_000},
    )

    response = await put(client, document)

    assert response.status_code == 200
    body = response.json()
    assert (body["version"], body["created_by"]) == (2, "carol")
    assert datetime.fromisoformat(body["created_at"])
    assert body["policy"]["allowed_mime_types"] == ["application/pdf", "image/*"]
    assert (await client.get("/api/v1/policies", auth=CAROL)).json() == body
    # The previous version and its author are kept.
    first, second = await versions(resources)
    assert (first.version, first.created_by, first.document) == (1, "watchman", DEFAULT)
    assert (second.version, second.created_by) == (2, "carol")
    assert second.document["max_size_bytes"] == 20_000_000


@pytest.mark.parametrize(
    ("document", "location"),
    [
        (changed(max_size_bytes=0), ["body", "max_size_bytes"]),
        (changed(blocked_extensions=["exe"]), ["body", "blocked_extensions", 0]),
        ({"max_size_bytes": 1000}, ["body", "allowed_mime_types"]),
        ("not a policy", ["body"]),
    ],
    ids=["zero-size", "extension-without-dot", "incomplete", "not-an-object"],
)
async def test_invalid_policy(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    document: object,
    location: list,
) -> None:
    response = await put(client, document)

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "https://alexandria.test/problems/validation-error"
    assert location in [error["loc"] for error in body["errors"]]
    assert [row.version for row in await versions(resources)] == [1]


async def test_only_curators_read_and_replace_the_policy(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    assert (await client.get("/api/v1/policies", auth=ALICE)).status_code == 403
    assert (await put(client, DEFAULT, auth=ALICE)).status_code == 403
    assert (await client.get("/api/v1/policies")).status_code == 401
    assert (await put(client, DEFAULT, auth=None)).status_code == 401
    assert [row.version for row in await versions(resources)] == [1]


async def test_versions_follow_each_other_when_curators_save_at_once(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    answers = await asyncio.gather(*(put(client, DEFAULT) for _ in range(4)))

    assert sorted(answer.json()["version"] for answer in answers) == [2, 3, 4, 5]
    assert [row.version for row in await versions(resources)] == [1, 2, 3, 4, 5]


async def test_a_new_policy_only_applies_to_new_submissions(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    before = await submit(client, content=samples.TEXT, filename="notes.txt")
    assert before.status_code == 202

    smaller = {"max_size_bytes": 10, "auto_approve": {"groups": [], "max_size_bytes": 10}}
    assert (await put(client, changed(**smaller))).status_code == 200
    after = await submit(client, content=samples.TEXT, filename="notes.txt")

    assert after.status_code == 422
    assert after.json()["reason"] == "file_too_large"
    versions_used = {s.id: (s.status, s.policy_version) for s in await stored(resources)}
    assert versions_used == {
        before.json()["id"]: ("pending_review", 1),
        after.json()["submission_id"]: ("rejected", 2),
    }
