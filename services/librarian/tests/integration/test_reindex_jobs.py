"""Re-index jobs, as the indexer runs them."""

from datetime import UTC, datetime, timedelta

from ulid import ULID

from librarian.db.models import Document, ReindexJob
from librarian.indexer import LEASE, Indexer
from librarian.resources import Resources
from tests.integration.conftest import FakeLibrary, document_of, index, index_all


async def add_job(resources: Resources, collection: str | None = None, **values: object) -> str:
    job = ReindexJob(
        id=f"job_{ULID()}",
        collection=collection,
        requested_by="carol",
        **({"status": "queued"} | values),
    )
    async with resources.sessions.begin() as session:
        session.add(job)
    return job.id


async def job_of(resources: Resources, job_id: str) -> ReindexJob:
    async with resources.sessions() as session:
        job = await session.get(ReindexJob, job_id)
    assert job is not None
    return job


async def in_library(resources: Resources, library: FakeLibrary, path: str, text: str) -> Document:
    """An indexed document whose file is in the library."""
    document = await index(resources, path, text)
    library.files[path] = (document.file_id, text.encode())
    return document


async def test_a_job_indexes_the_whole_library_again(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    manual = await in_library(resources, library, "Library/Manuals/washer.md", "Old passage.")
    invoice = await in_library(resources, library, "Library/Invoices/power.md", "Power.")
    library.files["Library/Manuals/washer.md"] = (manual.file_id, b"New passage.")
    job_id = await add_job(resources)

    assert await indexer.start_next_job()

    job = await job_of(resources, job_id)
    assert (job.status, job.documents) == ("running", 2)
    assert job.started_at is not None
    queued = await document_of(resources, manual.file_id)
    assert queued is not None
    # Still searchable while it waits.
    assert (queued.status, queued.job_id) == ("indexed", job_id)

    assert await index_all(indexer) == 2
    await indexer.finish_jobs()

    job = await job_of(resources, job_id)
    assert job.status == "done"
    assert job.finished_at is not None
    for document in (manual, invoice):
        after = await document_of(resources, document.file_id)
        assert after is not None
        assert (after.status, after.job_id) == ("indexed", None)
    assert sorted(library.downloads) == ["Library/Invoices/power.md", "Library/Manuals/washer.md"]


async def test_a_job_for_one_collection_queues_only_its_documents(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    manual = await in_library(resources, library, "Library/Manuals/washer.md", "Washer.")
    invoice = await in_library(resources, library, "Library/Invoices/power.md", "Power.")
    job_id = await add_job(resources, "Manuals")

    await indexer.start_next_job()

    assert (await job_of(resources, job_id)).documents == 1
    queued = await document_of(resources, manual.file_id)
    left = await document_of(resources, invoice.file_id)
    assert queued is not None
    assert left is not None
    assert queued.next_attempt_at is not None
    assert left.next_attempt_at is None


async def test_a_job_also_adds_new_files_and_drops_gone_ones(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    gone = await index(resources, "Library/Manuals/gone.md", "Gone.")
    new_file = library.add("Library/Manuals/new.md", b"New.")
    job_id = await add_job(resources, "Manuals")

    await indexer.start_next_job()
    await index_all(indexer)
    await indexer.finish_jobs()

    assert await document_of(resources, gone.file_id) is None
    document = await document_of(resources, new_file)
    assert document is not None
    assert document.status == "indexed"
    job = await job_of(resources, job_id)
    assert (job.status, job.documents) == ("done", 1)


async def test_a_failed_document_does_not_hold_up_the_job(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    broken = await in_library(resources, library, "Library/Manuals/broken.pdf", "Was fine.")
    library.files["Library/Manuals/broken.pdf"] = (broken.file_id, b"%PDF-1.4\nbroken\n")
    job_id = await add_job(resources)

    await indexer.start_next_job()
    await index_all(indexer)
    await indexer.finish_jobs()

    assert (await job_of(resources, job_id)).status == "done"
    document = await document_of(resources, broken.file_id)
    assert document is not None
    assert (document.status, document.attempts, document.job_id) == ("failed", 1, None)


async def test_a_job_whose_documents_were_removed_is_done(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    document = await in_library(resources, library, "Library/Manuals/washer.md", "Washer.")
    job_id = await add_job(resources)
    await indexer.start_next_job()
    # file.removed arrives before the indexer gets to the document.
    async with resources.sessions.begin() as session:
        await session.delete(await session.get(Document, document.id))

    await indexer.finish_jobs()

    assert (await job_of(resources, job_id)).status == "done"


async def test_a_job_for_a_collection_that_does_not_exist_fails(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    job_id = await add_job(resources, "Recipes")

    await indexer.start_next_job()

    job = await job_of(resources, job_id)
    assert (job.status, job.error) == ("failed", "There is no collection Recipes in the library")
    assert job.finished_at is not None


async def test_a_job_fails_when_the_library_cannot_be_listed(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    library.status = 503
    job_id = await add_job(resources)

    await indexer.start_next_job()

    job = await job_of(resources, job_id)
    assert job.status == "failed"
    assert job.error == "Cannot list the library: PROPFIND Library: HTTP 503"


async def test_jobs_start_in_the_order_they_were_asked(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    now = datetime.now(UTC)
    second = await add_job(resources, created_at=now)
    first = await add_job(resources, created_at=now - timedelta(minutes=1))

    assert await indexer.start_next_job()
    assert (await job_of(resources, first)).status == "running"
    assert (await job_of(resources, second)).status == "queued"
    assert await indexer.start_next_job()
    assert not await indexer.start_next_job()


async def test_a_job_left_starting_by_a_stopped_process_fails(
    resources: Resources, indexer: Indexer
) -> None:
    stale = await add_job(
        resources, status="running", started_at=datetime.now(UTC) - LEASE - timedelta(seconds=1)
    )
    starting = await add_job(resources, status="running", started_at=datetime.now(UTC))

    await indexer.finish_jobs()

    job = await job_of(resources, stale)
    assert job.status == "failed"
    assert job.error == "librarian stopped while it was starting the job"
    assert (await job_of(resources, starting)).status == "running"
