"""Members are notified in Nextcloud when their submission is published or
rejected. Real PostgreSQL and Redis, Nextcloud mocked with respx."""

import json

import httpx
import respx

from tests.conftest import basic_auth
from tests.integration.conftest import DAVE, FILE_ID, stored, submit
from tests.integration.test_review import approve, pending, reject
from tests.integration.test_tracking import withdraw
from watchman.mover import Mover
from watchman.resources import Resources


def sent(mocked: respx.MockRouter) -> list[tuple[str, dict]]:
    """(user, body) of every notification sent so far."""
    return [
        (call.request.url.path.rsplit("/", 1)[-1], json.loads(call.request.content))
        for call in mocked["notify"].calls
    ]


def text(body: dict, part: str) -> str:
    template, parameters = body[part], body[f"{part}Parameters"]
    for key, value in parameters.items():
        template = template.replace("{" + key + "}", value["name"])
    return template


async def test_rejection(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    submission_id = await pending(client)

    response = await reject(client, submission_id, reason="Scanned upside down.")

    assert response.status_code == 200
    [(user, body)] = sent(mocked)
    assert user == "alice"
    assert text(body, "subject") == "Your file warranty.pdf was rejected"
    assert text(body, "message") == "carol rejected it. Reason: Scanned upside down."
    request = mocked["notify"].calls.last.request
    assert request.headers["Authorization"] == basic_auth("alexandria", "service-password")


async def test_approval_is_notified_once_published(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)

    await approve(client, submission_id)
    assert not mocked["notify"].called

    assert await Mover(resources).publish_next()

    [(user, body)] = sent(mocked)
    assert user == "alice"
    assert text(body, "subject") == "Your file warranty.pdf is in the library"
    assert text(body, "message") == (
        "carol approved it, and it is now at Library/Manuals/warranty.pdf."
    )
    assert body["subjectParameters"]["file"]["link"] == f"https://alexandria.test/f/{FILE_ID}"
    # Sent before the empty quarantine folder is deleted, the mover's last step.
    notified = mocked.calls.index(mocked["notify"].calls.last)
    deleted = mocked.calls.index(mocked["delete"].calls.last)
    assert notified < deleted


async def test_automatic_approval(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    await submit(client, auth=DAVE)

    assert await Mover(resources).publish_next()

    [(user, body)] = sent(mocked)
    assert user == "dave"
    assert text(body, "message") == (
        "It was approved automatically, and it is now at Library/Manuals/warranty.pdf."
    )


async def test_no_notification_for_a_failed_attempt(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)
    await approve(client, submission_id)
    mocked["move"].respond(503)

    assert await Mover(resources).publish_next()

    [submission] = await stored(resources)
    assert submission.status == "approved"
    assert not mocked["notify"].called


async def test_no_notification_for_submitting_or_withdrawing(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)

    assert (await withdraw(client, submission_id)).status_code == 204

    assert not mocked["notify"].called


async def test_rejection_stands_when_nextcloud_refuses_the_notification(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)
    mocked["notify"].side_effect = httpx.ConnectError("refused")

    response = await reject(client, submission_id, reason="Not relevant.")

    assert response.status_code == 200
    [submission] = await stored(resources)
    assert submission.status == "rejected"


async def test_publication_stands_when_nextcloud_refuses_the_notification(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    submission_id = await pending(client)
    await approve(client, submission_id)
    mocked["notify"].respond(400)

    assert await Mover(resources).publish_next()

    [submission] = await stored(resources)
    assert submission.status == "published"
    assert mocked["delete"].called
