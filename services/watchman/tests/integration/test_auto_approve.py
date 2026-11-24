"""Submissions approved by the policy, without a curator: real PostgreSQL and
Redis, Nextcloud and observer mocked with respx."""

import httpx
import pytest
import respx

from tests import samples
from tests.integration.conftest import ALICE, DAVE, events, stored, submit, use_policy
from tests.integration.test_review import history
from watchman.mover import Mover
from watchman.resources import Resources


async def test_trusted_member_is_approved_and_published(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    resources.mover_wakeup.clear()

    response = await submit(client, auth=DAVE)

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "approved"
    assert [check["result"] for check in body["checks"]] == ["pass"] * 5
    assert mocked["put"].called
    [change] = await history(resources, body["id"])
    assert (change.from_status, change.to_status, change.actor, change.reason) == (
        "checking",
        "approved",
        "watchman",
        "auto_approve",
    )
    assert resources.mover_wakeup.is_set()
    [created] = await events(resources)
    assert (created["type"], created["actor"]) == ("submission.created", "dave")
    assert created["data"]["status"] == "approved"

    # Due at once: the mover publishes it without a curator.
    assert await Mover(resources).publish_next()

    [submission] = await stored(resources)
    assert submission.status == "published"
    assert submission.library_path == "Library/Manuals/warranty.pdf"
    assert [change.to_status for change in await history(resources, body["id"])] == [
        "approved",
        "published",
    ]
    published = (await events(resources))[-1]
    assert (published["type"], published["actor"]) == ("file.published", "watchman")
    assert published["data"]["submitted_by"] == "dave"


async def test_members_outside_the_trusted_groups_wait_for_a_curator(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    response = await submit(client, auth=ALICE)

    assert response.json()["status"] == "pending_review"
    assert not await Mover(resources).publish_next()


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectError("refused"), httpx.ReadTimeout("slow")],
    ids=["down", "timeout"],
)
async def test_never_approved_automatically_without_the_quota_check(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    failure: Exception,
) -> None:
    mocked["quota"].mock(side_effect=failure)

    response = await submit(client, auth=DAVE)

    assert response.status_code == 202
    assert response.json()["status"] == "pending_review"
    assert response.json()["checks"][-1]["result"] == "unavailable"
    [change] = await history(resources, response.json()["id"])
    assert (change.to_status, change.actor, change.reason) == ("pending_review", "dave", None)


async def test_files_over_the_auto_approve_limit_wait_for_a_curator(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    limit = len(samples.PDF) - 1
    await use_policy(resources, auto_approve={"groups": ["trusted"], "max_size_bytes": limit})

    response = await submit(client, auth=DAVE)

    assert response.json()["status"] == "pending_review"


async def test_a_failed_check_still_rejects(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    response = await submit(client, auth=DAVE, content=samples.EXE, filename="setup.exe")

    assert response.status_code == 422
    assert response.json()["reason"] == "blocked_type"
    [submission] = await stored(resources)
    assert submission.status == "rejected"
    assert not mocked["put"].called


async def test_policy_changes_apply_to_auto_approve(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    await use_policy(resources, auto_approve={"groups": [], "max_size_bytes": 1000})
    off = await submit(client, auth=DAVE, content=samples.TEXT, filename="notes.txt")
    await use_policy(resources, auto_approve={"groups": ["members"], "max_size_bytes": 1000})
    members = await submit(client, auth=ALICE, content=samples.TEXT, filename="notes.txt")

    assert off.json()["status"] == "pending_review"
    assert members.json()["status"] == "approved"
