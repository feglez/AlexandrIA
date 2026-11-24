"""GET /api/v1/submissions and the curator decisions (approve, reject) with a
real PostgreSQL and Redis. Nextcloud is mocked with respx."""

import asyncio

import httpx
import pytest
import respx
from sqlalchemy import select

from tests import samples
from tests.integration.conftest import ALICE, BOB, CAROL, DAV_PATH, events, stored, submit
from watchman.api import review
from watchman.db.models import StatusChange
from watchman.resources import Resources


async def pending(client: httpx.AsyncClient, auth: tuple[str, str] = ALICE, **fields) -> str:
    response = await submit(client, auth=auth, **fields)
    assert response.status_code == 202, response.json()
    return response.json()["id"]


async def approve(client: httpx.AsyncClient, submission_id: str, auth=CAROL) -> httpx.Response:
    return await client.post(f"/api/v1/submissions/{submission_id}/approve", auth=auth)


async def reject(
    client: httpx.AsyncClient, submission_id: str, auth=CAROL, **body
) -> httpx.Response:
    return await client.post(f"/api/v1/submissions/{submission_id}/reject", json=body, auth=auth)


async def history(resources: Resources, submission_id: str) -> list[StatusChange]:
    async with resources.sessions() as session:
        return list(
            await session.scalars(
                select(StatusChange)
                .where(StatusChange.submission_id == submission_id)
                .order_by(StatusChange.id)
            )
        )


async def test_curator_lists_pending_submissions(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    first = await pending(client, note="Warranty")
    second = await pending(client, auth=BOB, content=samples.TEXT, filename="notes.txt")
    await submit(client, content=samples.EXE, filename="setup.exe")  # rejected

    response = await client.get("/api/v1/submissions?status=pending_review", auth=CAROL)

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (2, 20, 0)
    assert [item["id"] for item in body["items"]] == [second, first]
    item = body["items"][1]
    assert item["submitted_by"] == "alice"
    assert item["size_bytes"] == len(samples.PDF)
    assert item["mime_type"] == "application/pdf"
    assert item["note"] == "Warranty"
    assert [check["result"] for check in item["checks"]] == ["pass"] * 5


async def test_members_only_see_their_own_submissions(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    mine = await pending(client)
    await pending(client, auth=BOB, content=samples.TEXT, filename="notes.txt")

    response = await client.get("/api/v1/submissions", auth=ALICE)

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [mine]
    assert response.json()["total"] == 1


async def test_list_pages(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    ids = [await pending(client, content=samples.TEXT, filename=f"{n}.txt") for n in range(3)]

    response = await client.get("/api/v1/submissions?limit=1&offset=1", auth=CAROL)

    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (3, 1, 1)
    assert [item["id"] for item in body["items"]] == [ids[1]]


@pytest.mark.parametrize("query", ["status=lost", "limit=0", "limit=101", "offset=-1"], ids=str)
async def test_list_invalid_query(
    client: httpx.AsyncClient, mocked: respx.MockRouter, query: str
) -> None:
    response = await client.get(f"/api/v1/submissions?{query}", auth=CAROL)

    assert response.status_code == 422
    assert response.json()["type"] == "https://alexandria.test/problems/validation-error"


async def test_list_needs_credentials(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    response = await client.get("/api/v1/submissions")

    assert response.status_code == 401


async def test_approve(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)

    response = await approve(client, submission_id)

    assert response.status_code == 200
    assert response.json()["id"] == submission_id
    assert response.json()["status"] == "approved"
    [submission] = await stored(resources)
    assert submission.status == "approved"
    assert submission.next_attempt_at is not None
    *_, change = await history(resources, submission_id)
    assert (change.from_status, change.to_status, change.actor) == (
        "pending_review",
        "approved",
        "carol",
    )
    assert change.reason is None
    assert change.changed_at == submission.updated_at
    *_, event = await events(resources)
    assert event["type"] == "submission.decided"
    assert event["actor"] == "carol"
    assert event["data"]["submission_id"] == submission_id
    assert event["data"]["decision"] == "approved"
    assert event["data"]["submitted_by"] == "alice"
    # The mover is told at once.
    assert resources.mover_wakeup.is_set()
    assert not mocked["move"].called


async def test_reject(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)

    response = await reject(client, submission_id, reason="  Scanned upside down.  ")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    deleted = mocked["delete"].calls.last.request
    assert deleted.url.path == f"{DAV_PATH}/Quarantine/{submission_id}"
    [submission] = await stored(resources)
    assert submission.status == "rejected"
    assert submission.quarantine_path is None
    *_, change = await history(resources, submission_id)
    assert (change.from_status, change.to_status, change.actor, change.reason) == (
        "pending_review",
        "rejected",
        "carol",
        "Scanned upside down.",
    )
    *_, event = await events(resources)
    assert event["type"] == "submission.decided"
    assert event["data"]["decision"] == "rejected"
    assert event["data"]["reason"] == "Scanned upside down."


@pytest.mark.parametrize(
    "body",
    [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 1001}, {"reason": 3}],
    ids=["missing", "empty", "blank", "too-long", "not-text"],
)
async def test_reject_needs_a_reason(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, body: dict
) -> None:
    submission_id = await pending(client)

    response = await reject(client, submission_id, **body)

    assert response.status_code == 422
    assert response.json()["errors"][0]["loc"][:2] == ["body", "reason"]
    assert not mocked["delete"].called
    [submission] = await stored(resources)
    assert submission.status == "pending_review"


async def test_reject_without_a_body(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    submission_id = await pending(client)

    response = await client.post(f"/api/v1/submissions/{submission_id}/reject", auth=CAROL)

    assert response.status_code == 422


async def test_reject_when_nextcloud_cannot_delete(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review, "DELETE_RETRY_DELAYS", (0, 0))
    submission_id = await pending(client)
    mocked["delete"].respond(503)

    response = await reject(client, submission_id, reason="Not relevant.")

    assert response.status_code == 503
    assert mocked["delete"].call_count == 3
    [submission] = await stored(resources)
    assert submission.status == "pending_review"
    assert submission.quarantine_path is not None
    assert len(await history(resources, submission_id)) == 1


async def test_reject_retries_the_delete(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review, "DELETE_RETRY_DELAYS", (0, 0))
    submission_id = await pending(client)
    mocked["delete"].side_effect = [httpx.ConnectError("refused"), httpx.Response(404)]

    response = await reject(client, submission_id, reason="Not relevant.")

    assert response.status_code == 200
    assert mocked["delete"].call_count == 2


@pytest.mark.parametrize("auth", [ALICE, BOB], ids=["owner", "other-member"])
async def test_only_curators_decide(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    auth: tuple[str, str],
) -> None:
    submission_id = await pending(client)

    approved = await approve(client, submission_id, auth=auth)
    rejected = await reject(client, submission_id, auth=auth)  # no reason: still 403

    assert approved.status_code == rejected.status_code == 403
    [submission] = await stored(resources)
    assert submission.status == "pending_review"
    assert not mocked["delete"].called


@pytest.mark.parametrize("decide", [approve, reject], ids=["approve", "reject"])
async def test_decision_needs_credentials(
    client: httpx.AsyncClient, mocked: respx.MockRouter, decide
) -> None:
    submission_id = await pending(client)

    response = await decide(client, submission_id, auth=None)

    assert response.status_code == 401


async def test_unknown_submission(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    approved = await approve(client, "sub_01JB8Z5Y0F")
    rejected = await reject(client, "sub_01JB8Z5Y0F", reason="Not relevant.")

    assert approved.status_code == rejected.status_code == 404


async def test_decided_submissions_cannot_be_decided_again(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    approved_id = await pending(client)
    rejected_id = await pending(client, content=samples.TEXT, filename="notes.txt")
    await approve(client, approved_id)
    await reject(client, rejected_id, reason="Not relevant.")

    answers = [
        await approve(client, approved_id),
        await reject(client, approved_id, reason="Too late."),
        await approve(client, rejected_id),
        await reject(client, rejected_id, reason="Again."),
    ]

    assert [answer.status_code for answer in answers] == [409] * 4
    body = answers[2].json()
    assert body["type"] == "https://alexandria.test/problems/invalid-transition"
    assert body["current_status"] == "rejected"
    assert body["detail"] == "The submission is rejected, so it cannot be approved."
    assert len(await history(resources, approved_id)) == 2


async def test_check_failures_cannot_be_approved(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await submit(client, content=samples.EXE, filename="setup.exe")

    assert (await approve(client, response.json()["submission_id"])).status_code == 409


async def test_two_curators_at_the_same_time(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)
    deleting = asyncio.Event()

    async def slow_delete(request: httpx.Request) -> httpx.Response:
        deleting.set()
        await asyncio.sleep(0.3)  # time for the approval to try
        return httpx.Response(204)

    async def approve_later() -> httpx.Response:
        await deleting.wait()  # the rejection is waiting for Nextcloud
        return await approve(client, submission_id)

    mocked["delete"].side_effect = slow_delete
    rejected, approved = await asyncio.gather(
        reject(client, submission_id, reason="No."), approve_later()
    )

    assert (rejected.status_code, approved.status_code) == (200, 409)
    assert approved.json()["current_status"] == "rejected"
    assert len(await history(resources, submission_id)) == 2
