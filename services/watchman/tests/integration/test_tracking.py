"""Members follow their submissions: the list filters, the detail with the
status history, and withdrawals. Real PostgreSQL and Redis, Nextcloud mocked
with respx."""

import httpx
import pytest
import respx

from tests import samples
from tests.integration.conftest import ALICE, BOB, CAROL, DAV_PATH, DAVE, stored, submit
from tests.integration.test_review import approve, history, pending, reject
from watchman.api import review
from watchman.mover import Mover
from watchman.resources import Resources


async def listed(client: httpx.AsyncClient, query: str, auth: tuple[str, str]) -> list[str]:
    response = await client.get(f"/api/v1/submissions?{query}", auth=auth)
    assert response.status_code == 200, response.json()
    return [item["id"] for item in response.json()["items"]]


async def detail(
    client: httpx.AsyncClient, submission_id: str, auth: tuple[str, str] = ALICE
) -> httpx.Response:
    return await client.get(f"/api/v1/submissions/{submission_id}", auth=auth)


async def withdraw(
    client: httpx.AsyncClient, submission_id: str, auth: tuple[str, str] | None = ALICE
) -> httpx.Response:
    return await client.delete(f"/api/v1/submissions/{submission_id}", auth=auth)


async def test_mine(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    alices = await pending(client)
    bobs = await pending(client, auth=BOB, content=samples.TEXT, filename="notes.txt")
    carols = await pending(client, auth=CAROL, content=samples.PNG, filename="photo.png")

    assert await listed(client, "mine=true", ALICE) == [alices]
    assert await listed(client, "mine=true", BOB) == [bobs]
    # A curator sees everyone's submissions, or only her own with mine=true.
    assert await listed(client, "", CAROL) == [carols, bobs, alices]
    assert await listed(client, "mine=true", CAROL) == [carols]
    assert await listed(client, "mine=false", CAROL) == [carols, bobs, alices]


async def test_submitted_by(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    alices = await pending(client)
    await pending(client, auth=BOB, content=samples.TEXT, filename="notes.txt")

    assert await listed(client, "submitted_by=alice", CAROL) == [alices]
    assert await listed(client, "submitted_by=alice&mine=true", CAROL) == []
    # Members only ever see their own submissions.
    assert await listed(client, "submitted_by=bob", ALICE) == []
    assert await listed(client, "submitted_by=alice", ALICE) == [alices]


async def test_filters_together(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    kept = await pending(client)
    rejected = await pending(client, content=samples.TEXT, filename="notes.txt")
    await reject(client, rejected, reason="Not relevant.")
    await pending(client, auth=BOB, content=samples.PNG, filename="photo.png")

    assert await listed(client, "mine=true&status=pending_review", ALICE) == [kept]
    assert await listed(client, "submitted_by=alice&status=rejected", CAROL) == [rejected]
    page = await client.get("/api/v1/submissions?submitted_by=alice&limit=1&offset=1", auth=CAROL)
    assert page.json()["total"] == 2
    assert [item["id"] for item in page.json()["items"]] == [kept]


async def test_submitted_by_cannot_be_empty(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await client.get("/api/v1/submissions?submitted_by=", auth=CAROL)

    assert response.status_code == 422


async def test_detail_with_history(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client, note="Warranty")
    await approve(client, submission_id)
    await Mover(resources).publish_next()

    response = await detail(client, submission_id)

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["status"], body["submitted_by"]) == (
        submission_id,
        "published",
        "alice",
    )
    assert body["note"] == "Warranty"
    assert [check["name"] for check in body["checks"]] == [
        "size_limit",
        "mime_consistency",
        "blocked_type",
        "duplicate",
        "quota",
    ]
    assert [
        (change["from_status"], change["to_status"], change["actor"], change["reason"])
        for change in body["history"]
    ] == [
        ("checking", "pending_review", "alice", None),
        ("pending_review", "approved", "carol", None),
        ("approved", "published", "watchman", None),
    ]
    stored_changes = await history(resources, submission_id)
    assert [change["changed_at"] for change in body["history"]] == [
        change.changed_at.isoformat().replace("+00:00", "Z") for change in stored_changes
    ]


async def test_detail_of_a_rejection(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    rejected = await submit(client, content=samples.EXE, filename="setup.exe")

    body = (await detail(client, rejected.json()["submission_id"])).json()

    assert body["status"] == "rejected"
    assert {check["name"]: check["result"] for check in body["checks"]}["blocked_type"] == "fail"
    [change] = body["history"]
    assert (change["to_status"], change["actor"], change["reason"]) == (
        "rejected",
        "alice",
        "blocked_type",
    )


async def test_detail_of_an_automatic_approval(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    submission_id = (await submit(client, auth=DAVE)).json()["id"]

    body = (await detail(client, submission_id, auth=DAVE)).json()

    [change] = body["history"]
    assert (change["from_status"], change["to_status"], change["actor"], change["reason"]) == (
        "checking",
        "approved",
        "watchman",
        "auto_approve",
    )


async def test_curators_see_any_detail(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    submission_id = await pending(client)

    response = await detail(client, submission_id, auth=CAROL)

    assert response.status_code == 200
    assert response.json()["submitted_by"] == "alice"


async def test_other_members_get_404(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    submission_id = await pending(client)

    hidden = await detail(client, submission_id, auth=BOB)
    unknown = await detail(client, "sub_01JB8Z5Y0F", auth=BOB)

    assert hidden.status_code == unknown.status_code == 404
    assert hidden.json()["detail"] == unknown.json()["detail"]


async def test_detail_needs_credentials(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)

    response = await client.get(f"/api/v1/submissions/{submission_id}")

    assert response.status_code == 401


async def test_withdraw(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)

    response = await withdraw(client, submission_id)

    assert response.status_code == 204
    assert response.content == b""
    deleted = mocked["delete"].calls.last.request
    assert deleted.url.path == f"{DAV_PATH}/Quarantine/{submission_id}"
    [submission] = await stored(resources)
    assert submission.status == "withdrawn"
    assert submission.quarantine_path is None
    *_, change = await history(resources, submission_id)
    assert (change.from_status, change.to_status, change.actor, change.reason) == (
        "pending_review",
        "withdrawn",
        "alice",
        None,
    )
    assert change.changed_at == submission.updated_at
    assert not await Mover(resources).publish_next()


async def test_decided_submissions_cannot_be_withdrawn(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    published = await pending(client)
    await approve(client, published)
    assert await Mover(resources).publish_next()
    approved = await pending(client, content=samples.PNG, filename="photo.png")
    await approve(client, approved)
    rejected = await pending(client, content=samples.TEXT, filename="notes.txt")
    await reject(client, rejected, reason="Not relevant.")
    mocked["delete"].reset()

    answers = [await withdraw(client, sub) for sub in (published, approved, rejected)]

    assert [answer.status_code for answer in answers] == [409] * 3
    assert [answer.json()["current_status"] for answer in answers] == [
        "published",
        "approved",
        "rejected",
    ]
    body = answers[1].json()
    assert body["type"] == "https://alexandria.test/problems/invalid-transition"
    assert body["detail"] == "The submission is approved, so it cannot be withdrawn."
    assert not mocked["delete"].called


async def test_withdraw_twice(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    submission_id = await pending(client)
    await withdraw(client, submission_id)

    again = await withdraw(client, submission_id)

    assert again.status_code == 409
    assert again.json()["current_status"] == "withdrawn"
    assert (await approve(client, submission_id)).status_code == 409


async def test_check_failures_cannot_be_withdrawn(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await submit(client, content=samples.EXE, filename="setup.exe")

    answer = await withdraw(client, response.json()["submission_id"])

    assert answer.status_code == 409
    assert answer.json()["current_status"] == "rejected"


async def test_only_the_owner_withdraws(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)

    other_member = await withdraw(client, submission_id, auth=BOB)
    curator = await withdraw(client, submission_id, auth=CAROL)

    assert other_member.status_code == 404
    assert curator.status_code == 403
    [submission] = await stored(resources)
    assert submission.status == "pending_review"
    assert not mocked["delete"].called


async def test_withdraw_unknown_submission(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    response = await withdraw(client, "sub_01JB8Z5Y0F")

    assert response.status_code == 404


async def test_withdraw_needs_credentials(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)

    response = await withdraw(client, submission_id, auth=None)

    assert response.status_code == 401


async def test_withdraw_when_nextcloud_cannot_delete(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(review, "DELETE_RETRY_DELAYS", (0, 0))
    submission_id = await pending(client)
    mocked["delete"].respond(503)

    response = await withdraw(client, submission_id)

    assert response.status_code == 503
    assert mocked["delete"].call_count == 3
    [submission] = await stored(resources)
    assert submission.status == "pending_review"
    assert submission.quarantine_path is not None
    assert len(await history(resources, submission_id)) == 1
