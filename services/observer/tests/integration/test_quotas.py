from datetime import UTC, datetime

import httpx
import pytest
import respx

from observer.consumer import EventConsumer
from observer.resources import Resources
from tests.integration.conftest import ALICE, BOB, CAROL, created, drain, emit, published

DEFAULT = {"max_contribution_bytes": 5 * 1024**3, "max_submissions_per_month": 50}
SMALL = {"max_contribution_bytes": 1000, "max_submissions_per_month": 3}


async def test_a_member_has_the_default_quota(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await client.get("/api/v1/quotas/alice", auth=ALICE)

    assert response.status_code == 200
    assert response.json() == {"user": "alice", **DEFAULT, "updated_by": None, "updated_at": None}


async def test_members_cannot_see_the_quota_of_others(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await client.get("/api/v1/quotas/bob", auth=ALICE)

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"


async def test_a_curator_sets_a_quota(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    before = datetime.now(UTC)
    response = await client.put("/api/v1/quotas/alice", json=SMALL, auth=CAROL)
    after = datetime.now(UTC)

    assert response.status_code == 200
    saved = response.json()
    assert saved | {"updated_at": None} == {
        "user": "alice",
        **SMALL,
        "updated_by": "carol",
        "updated_at": None,
    }
    assert before <= datetime.fromisoformat(saved["updated_at"]) <= after
    for credentials in (ALICE, CAROL):
        response = await client.get("/api/v1/quotas/alice", auth=credentials)
        assert response.json() == saved
    # Nobody else's quota changed.
    response = await client.get("/api/v1/quotas/bob", auth=BOB)
    assert response.json()["updated_by"] is None


async def test_a_new_quota_replaces_the_old_one(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    first = (await client.put("/api/v1/quotas/bob", json=SMALL, auth=CAROL)).json()
    bigger = {"max_contribution_bytes": 10 * 1024**3, "max_submissions_per_month": 100}

    response = await client.put("/api/v1/quotas/bob", json=bigger, auth=CAROL)

    assert response.status_code == 200
    assert response.json()["max_contribution_bytes"] == 10 * 1024**3
    assert response.json()["max_submissions_per_month"] == 100
    assert response.json()["updated_at"] >= first["updated_at"]
    assert (await client.get("/api/v1/quotas/bob", auth=BOB)).json() == response.json()


async def test_members_cannot_set_quotas(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await client.put(
        "/api/v1/quotas/alice", json=DEFAULT | {"max_submissions_per_month": 999}, auth=ALICE
    )

    assert response.status_code == 403
    assert (await client.get("/api/v1/quotas/alice", auth=ALICE)).json()["updated_by"] is None


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"max_submissions_per_month": 3}, "max_contribution_bytes"),
        ({"max_contribution_bytes": 1000}, "max_submissions_per_month"),
        (SMALL | {"max_contribution_bytes": -1}, "max_contribution_bytes"),
        (SMALL | {"max_submissions_per_month": -1}, "max_submissions_per_month"),
        (SMALL | {"max_contribution_bytes": "lots"}, "max_contribution_bytes"),
        (SMALL | {"max_contribution_bytes": 1.5}, "max_contribution_bytes"),
        (SMALL | {"max_contribution_bytes": 2**63}, "max_contribution_bytes"),
        (SMALL | {"max_submissions_per_month": 2**31}, "max_submissions_per_month"),
        (SMALL | {"max_size_bytes": 10}, "max_size_bytes"),
    ],
)
async def test_invalid_quotas(
    client: httpx.AsyncClient, mocked: respx.MockRouter, body: dict, field: str
) -> None:
    response = await client.put("/api/v1/quotas/alice", json=body, auth=CAROL)

    assert response.status_code == 422
    assert ["body", field] in [error["loc"] for error in response.json()["errors"]]
    assert (await client.get("/api/v1/quotas/alice", auth=ALICE)).json()["updated_by"] is None


async def test_credentials_are_required(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    assert (await client.get("/api/v1/quotas/alice")).status_code == 401
    assert (await client.put("/api/v1/quotas/alice", json=SMALL)).status_code == 401


async def test_usage_shows_the_member_s_quota(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    await emit(resources, "file.published", published("alice", 400))
    await emit(resources, "submission.created", created("bob"))
    await drain(consumer)
    await client.put("/api/v1/quotas/alice", json=SMALL, auth=CAROL)

    mine = (await client.get("/api/v1/usage/me", auth=ALICE)).json()
    everyone = (await client.get("/api/v1/usage", auth=CAROL)).json()

    assert mine["library_contribution"] == {"used_bytes": 400, "quota_bytes": 1000}
    assert mine["submissions"] == {"this_period": 0, "limit": 3}
    alice, bob = everyone["items"]
    assert (alice["user"], alice["library_contribution"]["quota_bytes"]) == ("alice", 1000)
    assert alice["submissions"]["limit"] == 3
    assert (bob["user"], bob["library_contribution"]["quota_bytes"]) == ("bob", 5 * 1024**3)
    assert bob["submissions"] == {"this_period": 1, "limit": 50}
