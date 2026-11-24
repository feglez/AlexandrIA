"""The mover publishes approved submissions: real PostgreSQL and Redis,
Nextcloud mocked with respx."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import select, update

from tests import samples
from tests.conftest import basic_auth
from tests.integration.conftest import DAV, DAV_PATH, FILE_ID, events, stored, submit
from tests.integration.test_review import approve, history, pending
from tests.unit.test_files import file_id_answer
from watchman.db.models import LibraryFile, Submission
from watchman.mover import Mover, target_names
from watchman.resources import Resources


@pytest.fixture
def mover(resources: Resources) -> Mover:
    return Mover(resources, max_attempts=3, retry_seconds=10)


async def approved(client: httpx.AsyncClient, **fields) -> str:
    submission_id = await pending(client, **fields)
    assert (await approve(client, submission_id)).status_code == 200
    return submission_id


async def make_due(resources: Resources) -> None:
    async with resources.sessions.begin() as session:
        await session.execute(
            update(Submission).values(next_attempt_at=datetime.now(UTC) - timedelta(seconds=1))
        )


async def library(resources: Resources) -> list[LibraryFile]:
    async with resources.sessions() as session:
        return list(await session.scalars(select(LibraryFile)))


def test_target_names() -> None:
    names = list(target_names("report.final.pdf"))

    assert names[:3] == ["report.final.pdf", "report.final (2).pdf", "report.final (3).pdf"]
    assert len(names) == 20
    assert list(target_names("README"))[1] == "README (2)"


async def test_publish(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    submission_id = await approved(client)

    assert await mover.publish_next()

    move = mocked["move"].calls.last.request
    assert move.url.path == f"{DAV_PATH}/Quarantine/{submission_id}/warranty.pdf"
    assert move.headers["Destination"] == f"{DAV}/Library/Manuals/warranty.pdf"
    assert move.headers["Overwrite"] == "F"
    assert move.headers["Authorization"] == basic_auth("alexandria", "service-password")
    [submission] = await stored(resources)
    assert submission.status == "published"
    assert submission.library_path == "Library/Manuals/warranty.pdf"
    assert submission.file_id == FILE_ID
    assert submission.quarantine_path is None
    assert submission.next_attempt_at is None
    [file] = await library(resources)
    assert (file.file_id, file.path, file.collection, file.submitted_by) == (
        FILE_ID,
        "Library/Manuals/warranty.pdf",
        "Manuals",
        "alice",
    )
    assert file.sha256 == hashlib.sha256(samples.PDF).hexdigest()
    assert file.submission_id == submission_id
    changes = [
        (c.from_status, c.to_status, c.actor) for c in await history(resources, submission_id)
    ]
    assert changes == [
        ("checking", "pending_review", "alice"),
        ("pending_review", "approved", "carol"),
        ("approved", "published", "watchman"),
    ]
    *_, event = await events(resources)
    assert event["type"] == "file.published"
    assert event["actor"] == "carol"
    assert event["data"] == {
        "submission_id": submission_id,
        "path": "Library/Manuals/warranty.pdf",
        "collection": "Manuals",
        "file_id": FILE_ID,
        "size_bytes": len(samples.PDF),
        "mime_type": "application/pdf",
        "sha256": hashlib.sha256(samples.PDF).hexdigest(),
        "submitted_by": "alice",
    }
    # The empty quarantine folder is removed.
    assert mocked["delete"].calls.last.request.url.path == f"{DAV_PATH}/Quarantine/{submission_id}"
    # Nothing else to do.
    assert not await mover.publish_next()


async def test_published_content_is_a_duplicate(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    await approved(client)
    await mover.publish_next()

    response = await submit(client, filename="copy.pdf")

    assert response.status_code == 422
    assert response.json()["reason"] == "duplicate"
    assert response.json()["existing_path"] == "Library/Manuals/warranty.pdf"


async def test_taken_names_get_a_number(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    mocked["move"].side_effect = [httpx.Response(412), httpx.Response(412), httpx.Response(201)]
    await approved(client)

    await mover.publish_next()

    destinations = [call.request.headers["Destination"] for call in mocked["move"].calls]
    assert destinations == [
        f"{DAV}/Library/Manuals/warranty.pdf",
        f"{DAV}/Library/Manuals/warranty%20%282%29.pdf",
        f"{DAV}/Library/Manuals/warranty%20%283%29.pdf",
    ]
    [submission] = await stored(resources)
    assert submission.library_path == "Library/Manuals/warranty (3).pdf"
    [file] = await library(resources)
    assert file.path == "Library/Manuals/warranty (3).pdf"


async def test_failed_move_is_retried_with_back_off(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    mocked["move"].respond(503)
    submission_id = await approved(client)

    before = datetime.now(UTC)
    assert await mover.publish_next()
    after = datetime.now(UTC)

    [submission] = await stored(resources)
    assert submission.status == "approved"
    assert submission.publish_attempts == 1
    assert submission.last_error == (
        f"MOVE Quarantine/{submission_id}/warranty.pdf to Library/Manuals/warranty.pdf: HTTP 503"
    )
    wait = timedelta(seconds=10)
    assert before + wait <= submission.next_attempt_at <= after + wait
    # Not due yet.
    assert not await mover.publish_next()

    await make_due(resources)
    before = datetime.now(UTC)
    await mover.publish_next()
    after = datetime.now(UTC)
    [submission] = await stored(resources)
    wait = timedelta(seconds=20)
    assert before + wait <= submission.next_attempt_at <= after + wait

    mocked["move"].respond(201)
    await make_due(resources)
    await mover.publish_next()
    [submission] = await stored(resources)
    assert submission.status == "published"
    assert submission.last_error is None


async def test_failed_after_the_last_attempt(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    mocked["move"].respond(409)  # the collection folder is gone
    submission_id = await approved(client)

    for _ in range(3):
        await make_due(resources)
        assert await mover.publish_next()

    [submission] = await stored(resources)
    assert submission.status == "failed"
    assert submission.next_attempt_at is None
    *_, change = await history(resources, submission_id)
    assert (change.from_status, change.to_status, change.actor) == (
        "approved",
        "failed",
        "watchman",
    )
    assert "HTTP 409" in change.reason
    assert await library(resources) == []
    await make_due(resources)
    assert not await mover.publish_next()

    # A curator approves it again to retry.
    mocked["move"].respond(201)
    response = await approve(client, submission_id)
    assert response.status_code == 200
    [submission] = await stored(resources)
    assert (submission.status, submission.publish_attempts) == ("approved", 0)
    assert await mover.publish_next()
    [submission] = await stored(resources)
    assert submission.status == "published"


async def test_move_that_worked_before_a_crash(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    # An earlier attempt moved the file, then stopped before recording it.
    submission_id = await approved(client)
    async with resources.sessions.begin() as session:
        await session.execute(
            update(Submission).values(
                library_path="Library/Manuals/warranty (2).pdf", file_id=FILE_ID
            )
        )
    mocked["source_id"].respond(404)
    mocked["target_id"].respond(
        207, text=file_id_answer(f"{DAV_PATH}/Library/Manuals/warranty%20(2).pdf", FILE_ID)
    )

    assert await mover.publish_next()

    assert not mocked["move"].called
    [submission] = await stored(resources)
    assert submission.status == "published"
    assert submission.library_path == "Library/Manuals/warranty (2).pdf"
    [file] = await library(resources)
    assert file.path == "Library/Manuals/warranty (2).pdf"
    assert (await history(resources, submission_id))[-1].to_status == "published"


@pytest.mark.parametrize("target", [None, 404, 999], ids=["no-record", "not-there", "other-file"])
async def test_quarantined_file_missing(
    client: httpx.AsyncClient,
    resources: Resources,
    mocked: respx.MockRouter,
    mover: Mover,
    target: int | None,
) -> None:
    await approved(client)
    mocked["source_id"].respond(404)
    if target is not None:
        async with resources.sessions.begin() as session:
            await session.execute(
                update(Submission).values(
                    library_path="Library/Manuals/warranty.pdf", file_id=FILE_ID
                )
            )
        if target == 404:
            mocked["target_id"].respond(404)
        else:
            mocked["target_id"].respond(207, text=file_id_answer("/x", target))

    await mover.publish_next()

    assert not mocked["move"].called
    [submission] = await stored(resources)
    assert submission.status == "approved"
    assert submission.publish_attempts == 1
    assert "is not in the quarantine" in submission.last_error
    assert await library(resources) == []


async def test_old_library_rows_are_replaced(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    # A file deleted in Nextcloud whose row is still here (no webhooks yet).
    async with resources.sessions.begin() as session:
        session.add(
            LibraryFile(
                file_id=17,
                path="Library/Manuals/warranty.pdf",
                collection="Manuals",
                sha256="0" * 64,
                size_bytes=1,
                submitted_by="bob",
            )
        )
    await approved(client)

    await mover.publish_next()

    [file] = await library(resources)
    assert (file.file_id, file.submitted_by) == (FILE_ID, "alice")


async def test_submission_taken_by_another_replica_is_skipped(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    await approved(client)

    async with resources.sessions.begin() as session:
        await session.execute(select(Submission).with_for_update())
        assert not await mover.publish_next()

    assert await mover.publish_next()


async def test_claimed_submission_waits_for_its_lease(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter, mover: Mover
) -> None:
    # A mover that died during the move leaves the submission claimed.
    await approved(client)
    claim = await mover._claim()
    assert claim is not None

    assert not await mover.publish_next()

    await make_due(resources)
    assert await mover.publish_next()
    [submission] = await stored(resources)
    assert (submission.status, submission.publish_attempts) == ("published", 2)


async def test_run_publishes_after_an_approval(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    task = asyncio.create_task(Mover(resources, poll_seconds=60).run())
    try:
        await approved(client)
        for _ in range(100):
            [submission] = await stored(resources)
            if submission.status == "published":
                break
            await asyncio.sleep(0.05)
        assert submission.status == "published"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_survives_errors(resources: Resources, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def broken() -> bool:
        nonlocal calls
        calls += 1
        raise RuntimeError("database down")

    mover = Mover(resources, poll_seconds=0.01)
    monkeypatch.setattr(mover, "publish_next", broken)
    task = asyncio.create_task(mover.run())
    for _ in range(200):
        if calls > 1:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls > 1
