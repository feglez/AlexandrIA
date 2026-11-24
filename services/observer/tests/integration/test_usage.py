from datetime import UTC, datetime

import httpx
import pytest
import respx

from observer.consumer import EventConsumer
from observer.ledger import period_of
from observer.resources import Resources
from tests.integration.conftest import (
    ALICE,
    BOB,
    CAROL,
    created,
    drain,
    emit,
    published,
)

DEFAULT_QUOTA = 5 * 1024**3
OCTOBER = datetime(2026, 10, 19, 17, 45, 2, tzinfo=UTC)
NOVEMBER = datetime(2026, 11, 3, 9, 0, 0, tzinfo=UTC)


def usage(contributed: int, submissions: int = 0, searches: int = 0, questions: int = 0) -> dict:
    return {
        "library_contribution": {"used_bytes": contributed, "quota_bytes": DEFAULT_QUOTA},
        "submissions": {"this_period": submissions, "limit": 50},
        "searches": {"this_period": searches},
        "questions": {"this_period": questions},
    }


async def test_a_member_without_usage(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    response = await client.get("/api/v1/usage/me", auth=ALICE)

    assert response.status_code == 200
    assert response.json() == {
        "user": "alice",
        "period": period_of(datetime.now(UTC)),
        "personal_storage": {"used_bytes": 8_321_499_136, "quota_bytes": None},
        **usage(0),
    }


async def test_my_usage_this_month(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    now = datetime.now(UTC)
    # Earlier months only count towards the contribution.
    await emit(resources, "file.published", published("bob", 100), occurred_at=OCTOBER)
    await emit(resources, "submission.created", created("bob"), occurred_at=OCTOBER)
    await emit(resources, "search.performed", {}, actor="bob", occurred_at=OCTOBER)
    await emit(resources, "file.published", published("bob", 4000), occurred_at=now)
    await emit(resources, "file.published", published("bob", 23), actor="watchman", occurred_at=now)
    await emit(resources, "file.removed", published("bob", 100), occurred_at=now)
    await emit(resources, "submission.created", created("bob"), occurred_at=now)
    await emit(resources, "submission.created", created("bob", "approved"), occurred_at=now)
    await emit(resources, "submission.created", created("bob", "rejected"), occurred_at=now)
    await emit(resources, "search.performed", {}, actor="bob", occurred_at=now)
    await emit(resources, "question.answered", {}, actor="bob", occurred_at=now)
    # Someone else's usage.
    await emit(resources, "file.published", published("alice", 7), actor="bob", occurred_at=now)
    await emit(resources, "search.performed", {}, actor="alice", occurred_at=now)
    await drain(consumer)

    response = await client.get("/api/v1/usage/me", auth=BOB)

    assert response.status_code == 200
    assert response.json() == {
        "user": "bob",
        "period": period_of(now),
        "personal_storage": {"used_bytes": 523, "quota_bytes": 1_073_741_824},
        **usage(4023, submissions=2, searches=1, questions=1),
    }
    assert mocked["storage"].calls.last.request.url.path.endswith("/cloud/users/bob")


async def test_my_contribution_follows_the_events(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    async def contributed() -> int:
        await drain(consumer)
        response = await client.get("/api/v1/usage/me", auth=ALICE)
        return response.json()["library_contribution"]["used_bytes"]

    file = published("alice", 482_133)
    await emit(resources, "file.published", file, actor="carol")
    assert await contributed() == 482_133
    await emit(resources, "file.removed", file, actor="carol")
    assert await contributed() == 0


async def test_personal_storage_is_null_when_nextcloud_cannot_tell(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    # carol's account details are not in the mock: Nextcloud answers 404.
    response = await client.get("/api/v1/usage/me", auth=CAROL)

    assert response.status_code == 200
    assert response.json()["personal_storage"] is None
    assert response.json()["library_contribution"]["used_bytes"] == 0


async def test_nextcloud_down_for_the_storage(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    mocked["storage"].mock(side_effect=httpx.ConnectError("refused"))

    response = await client.get("/api/v1/usage/me", auth=ALICE)

    assert response.status_code == 200
    assert response.json()["personal_storage"] is None


@pytest.mark.parametrize("path", ["/api/v1/usage/me", "/api/v1/usage"])
async def test_credentials_are_required(
    client: httpx.AsyncClient, mocked: respx.MockRouter, path: str
) -> None:
    assert (await client.get(path)).status_code == 401
    assert (await client.get(path, auth=("alice", "wrong"))).status_code == 401
    assert not mocked["storage"].called


async def test_members_cannot_see_everyone_s_usage(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await client.get("/api/v1/usage", auth=ALICE)

    assert response.status_code == 403
    assert response.headers["Content-Type"] == "application/problem+json"


async def test_curators_see_every_member(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    await emit(resources, "file.published", published("bob", 100), occurred_at=OCTOBER)
    await emit(resources, "submission.created", created("bob"), occurred_at=OCTOBER)
    await emit(resources, "file.published", published("alice", 50), occurred_at=NOVEMBER)
    await emit(resources, "file.removed", published("bob", 100), occurred_at=NOVEMBER)
    await emit(resources, "search.performed", {}, actor="bob", occurred_at=NOVEMBER)
    await emit(resources, "search.performed", {}, actor="dave", occurred_at=NOVEMBER)
    await drain(consumer)

    october = await client.get("/api/v1/usage", params={"period": "2026-10"}, auth=CAROL)
    november = await client.get("/api/v1/usage", params={"period": "2026-11"}, auth=CAROL)

    assert october.status_code == 200
    assert october.json() == {
        "period": "2026-10",
        "items": [{"user": "bob", **usage(100, submissions=1)}],
        "total": 1,
        "limit": 20,
        "offset": 0,
    }
    assert november.json() == {
        "period": "2026-11",
        "items": [
            {"user": "alice", **usage(50)},
            {"user": "bob", **usage(0, searches=1)},
            {"user": "dave", **usage(0, searches=1)},
        ],
        "total": 3,
        "limit": 20,
        "offset": 0,
    }
    # The answer does not read anyone's storage.
    assert not mocked["storage"].called


async def test_the_member_list_is_paginated(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    for member in ("erin", "dave", "bob", "alice"):
        await emit(resources, "search.performed", {}, actor=member, occurred_at=NOVEMBER)
    await drain(consumer)

    response = await client.get(
        "/api/v1/usage", params={"period": "2026-11", "limit": 2, "offset": 1}, auth=CAROL
    )

    assert [item["user"] for item in response.json()["items"]] == ["bob", "dave"]
    assert response.json()["total"] == 4


async def test_the_default_period_is_this_month(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    await emit(resources, "search.performed", {}, actor="alice")
    await drain(consumer)

    response = await client.get("/api/v1/usage", auth=CAROL)

    assert response.json()["period"] == period_of(datetime.now(UTC))
    assert response.json()["items"] == [{"user": "alice", **usage(0, searches=1)}]


@pytest.mark.parametrize(
    "params",
    [
        {"period": "2026-13"},
        {"period": "2026-1"},
        {"period": "October"},
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
async def test_invalid_queries(
    client: httpx.AsyncClient, mocked: respx.MockRouter, params: dict
) -> None:
    response = await client.get("/api/v1/usage", params=params, auth=CAROL)

    assert response.status_code == 422
    assert response.json()["title"] == "Validation error"
