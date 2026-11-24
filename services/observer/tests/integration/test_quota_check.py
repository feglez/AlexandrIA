"""observer's side of the quota-check contract with watchman.

The example request and answer are in contracts/quota-check.json, which
watchman's tests use too.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from observer.consumer import EventConsumer
from observer.db.models import Quota
from observer.resources import Resources
from tests.integration.conftest import ALICE, CAROL, created, drain, emit, published

CONTRACT = json.loads(
    (Path(__file__).parents[4] / "contracts" / "quota-check.json").read_text(encoding="utf-8")
)
PATH = CONTRACT["request"]["path"]
OCTOBER = datetime(2026, 10, 19, 17, 45, 2, tzinfo=UTC)


async def set_quota(
    resources: Resources, member: str, max_bytes: int, max_submissions: int
) -> None:
    async with resources.sessions.begin() as session:
        session.add(
            Quota(
                member=member,
                max_contribution_bytes=max_bytes,
                max_submissions_per_month=max_submissions,
                updated_by="carol",
            )
        )


async def ask(internal: httpx.AsyncClient, user: str, size_bytes: int) -> dict:
    response = await internal.post(PATH, json={"user": user, "size_bytes": size_bytes})
    assert response.status_code == 200
    return response.json()


async def test_answer_matches_the_contract(
    internal: httpx.AsyncClient, resources: Resources, consumer: EventConsumer
) -> None:
    # alice's usage in README section 5.4: 1288490188 bytes in the library and
    # 14 submissions this month, with the default quota (5 GiB, 50).
    await emit(resources, "file.published", published("alice", 1_288_490_188))
    for _ in range(14):
        await emit(resources, "submission.created", created("alice"))
    await drain(consumer)
    request = CONTRACT["request"]

    response = await internal.request(request["method"], request["path"], json=request["body"])

    assert response.status_code == CONTRACT["response"]["status"]
    assert response.headers["content-type"] == request["headers"]["Content-Type"]
    assert response.json() == CONTRACT["response"]["body"]


async def test_a_member_without_usage(internal: httpx.AsyncClient) -> None:
    assert await ask(internal, "bob", 1000) == {
        "allowed": True,
        "remaining_bytes": 5 * 1024**3 - 1000,
        "remaining_submissions": 49,
    }


async def test_the_contribution_must_fit(
    internal: httpx.AsyncClient, resources: Resources, consumer: EventConsumer
) -> None:
    await set_quota(resources, "alice", 1000, 50)
    await emit(resources, "file.published", published("alice", 300), occurred_at=OCTOBER)
    await emit(resources, "file.published", published("alice", 200))
    await emit(resources, "file.published", published("alice", 100))
    await emit(resources, "file.removed", published("alice", 100))
    # Bytes contributed by others, or approved by alice, are not hers.
    await emit(resources, "file.published", published("bob", 900), actor="alice")
    await drain(consumer)

    # 500 bytes contributed over every month: 500 left.
    exact = await ask(internal, "alice", 500)
    over = await ask(internal, "alice", 501)

    assert exact == {"allowed": True, "remaining_bytes": 0, "remaining_submissions": 49}
    assert over == {"allowed": False, "remaining_bytes": 500, "remaining_submissions": 50}


async def test_the_submissions_of_this_month_must_fit(
    internal: httpx.AsyncClient, resources: Resources, consumer: EventConsumer
) -> None:
    await set_quota(resources, "alice", 5 * 1024**3, 3)
    for _ in range(5):
        await emit(resources, "submission.created", created("alice"), occurred_at=OCTOBER)
    await emit(resources, "submission.created", created("alice"))
    await emit(resources, "submission.created", created("alice", "approved"))
    # Rejected by the checks: it does not count.
    await emit(resources, "submission.created", created("alice", "rejected"))
    await drain(consumer)

    last = await ask(internal, "alice", 10)
    await emit(resources, "submission.created", created("alice"))
    await drain(consumer)
    refused = await ask(internal, "alice", 10)

    assert last == {
        "allowed": True,
        "remaining_bytes": 5 * 1024**3 - 10,
        "remaining_submissions": 0,
    }
    assert refused == {"allowed": False, "remaining_bytes": 5 * 1024**3, "remaining_submissions": 0}


async def test_a_quota_below_what_the_member_has(
    internal: httpx.AsyncClient, resources: Resources, consumer: EventConsumer
) -> None:
    await emit(resources, "file.published", published("alice", 400))
    await drain(consumer)
    await set_quota(resources, "alice", 100, 0)

    assert await ask(internal, "alice", 0) == {
        "allowed": False,
        "remaining_bytes": 0,
        "remaining_submissions": 0,
    }


async def test_a_new_quota_applies_to_the_next_check(
    internal: httpx.AsyncClient, client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    assert (await ask(internal, "alice", 2000))["allowed"] is True

    response = await client.put(
        "/api/v1/quotas/alice",
        json={"max_contribution_bytes": 1000, "max_submissions_per_month": 50},
        auth=CAROL,
    )
    assert response.status_code == 200

    assert await ask(internal, "alice", 2000) == {
        "allowed": False,
        "remaining_bytes": 1000,
        "remaining_submissions": 50,
    }


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer wrong-token",
        "Bearer observer-token-and-more",
        "Basic b2JzZXJ2ZXI6eA==",
        "observer-token",
    ],
    ids=["missing", "wrong", "longer", "basic", "no-scheme"],
)
async def test_the_service_token_is_required(
    internal: httpx.AsyncClient, authorization: str | None
) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    if authorization is None:
        del internal.headers["Authorization"]

    response = await internal.post(PATH, json={"user": "alice", "size_bytes": 1}, headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"] == "application/problem+json"


@pytest.mark.parametrize(
    "body",
    [{}, {"user": "alice"}, {"user": "", "size_bytes": 1}, {"user": "alice", "size_bytes": -1}],
)
async def test_invalid_requests(internal: httpx.AsyncClient, body: dict) -> None:
    response = await internal.post(PATH, json=body)

    assert response.status_code == 422


async def test_each_api_is_only_on_its_port(
    internal: httpx.AsyncClient, client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    public = await client.post(PATH, json={"user": "alice", "size_bytes": 1})
    usage = await internal.get("/api/v1/usage/me", auth=ALICE)

    assert public.status_code == 404
    assert usage.status_code == 404
