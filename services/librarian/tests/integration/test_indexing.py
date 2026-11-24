"""Indexing of the files watchman publishes: the consumer and the indexer."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from librarian.consumer import BLOCK_MS, EventConsumer
from librarian.indexer import SUPPORTED, Indexer
from librarian.resources import Resources
from tests.integration.conftest import (
    ALICE,
    PUBLIC_BASE_URL,
    FakeLibrary,
    document_of,
    drain,
    emit,
    index_all,
    make_due,
    passages_of,
    published,
)
from tests.samples import docx_file, pdf

MANUAL = b"# Washing machine\n\nThe warranty of the washing machine lasts three years.\n"


async def publish(resources: Resources, library: FakeLibrary, path: str, content: bytes) -> int:
    """The file reaches the library and watchman emits file.published."""
    file_id = library.add(path, content)
    await emit(resources, "file.published", published(file_id, path, content), actor="carol")
    return file_id


async def test_a_published_file_is_indexed_and_found(
    client: httpx.AsyncClient,
    resources: Resources,
    library: FakeLibrary,
    consumer: EventConsumer,
    indexer: Indexer,
) -> None:
    file_id = await publish(resources, library, "Library/Manuals/washer.md", MANUAL)

    assert await drain(consumer) == 1
    queued = await document_of(resources, file_id)
    assert queued is not None
    assert queued.status == "pending"
    assert queued.id.startswith("doc_")
    assert resources.indexer_wakeup.is_set()

    assert await index_all(indexer) == 1

    document = await document_of(resources, file_id)
    assert document is not None
    assert document.status == "indexed"
    assert (document.path, document.collection) == ("Library/Manuals/washer.md", "Manuals")
    assert document.chunks == 1
    assert document.sha256 == hashlib.sha256(MANUAL).hexdigest()
    assert document.indexed_at is not None
    assert document.error is None
    assert document.next_attempt_at is None
    assert await passages_of(resources, document.id) == [
        "# Washing machine\n\nThe warranty of the washing machine lasts three years."
    ]
    assert library.downloads == ["Library/Manuals/washer.md"]

    response = await client.get(
        "/api/v1/search", params={"q": "washing machine warranty"}, auth=ALICE
    )
    result = response.json()["results"][0]
    assert result["document_id"] == document.id
    assert result["url"] == f"{PUBLIC_BASE_URL}/f/{file_id}"


@pytest.mark.parametrize(
    ("path", "content", "text"),
    [
        ("Library/Manuals/washer.pdf", pdf("Garantía: tres años", "Page two"), "Garantía"),
        ("Library/Manuals/washer.docx", docx_file("Warranty: three years"), "three years"),
        ("Library/Manuals/washer.txt", "Garantía: tres años".encode(), "tres años"),
    ],
    ids=["pdf", "docx", "text"],
)
async def test_pdf_docx_and_text_files_are_read(
    resources: Resources,
    library: FakeLibrary,
    consumer: EventConsumer,
    indexer: Indexer,
    path: str,
    content: bytes,
    text: str,
) -> None:
    file_id = await publish(resources, library, path, content)
    await drain(consumer)
    await index_all(indexer)

    document = await document_of(resources, file_id)
    assert document is not None
    assert document.status == "indexed"
    assert text in " ".join(await passages_of(resources, document.id))


async def test_a_long_file_is_embedded_a_few_passages_at_a_time(
    resources: Resources, library: FakeLibrary, consumer: EventConsumer
) -> None:
    paragraphs = [f"Paragraph {number}: " + "washing machine " * 30 for number in range(20)]
    file_id = await publish(
        resources, library, "Library/Manuals/long.md", "\n\n".join(paragraphs).encode()
    )
    await drain(consumer)

    await index_all(Indexer(resources, batch=3))

    document = await document_of(resources, file_id)
    assert document is not None
    assert document.chunks > 3
    assert len(await passages_of(resources, document.id)) == document.chunks
    batches = resources.embedder.batches  # type: ignore[attr-defined]
    assert max(batches) == 3
    assert sum(batches) == document.chunks


async def test_other_types_are_skipped_without_reading_them(
    resources: Resources, library: FakeLibrary, consumer: EventConsumer, indexer: Indexer
) -> None:
    file_id = await publish(resources, library, "Library/Medical/scan.png", b"\x89PNG\r\n")
    await drain(consumer)
    await index_all(indexer)

    document = await document_of(resources, file_id)
    assert document is not None
    assert (document.status, document.error, document.chunks) == ("skipped", SUPPORTED, 0)
    assert document.next_attempt_at is None
    assert library.downloads == []


async def test_a_file_without_text_is_skipped(
    resources: Resources, library: FakeLibrary, consumer: EventConsumer, indexer: Indexer
) -> None:
    content = pdf(" ")
    file_id = await publish(resources, library, "Library/Medical/scan.pdf", content)
    await drain(consumer)
    await index_all(indexer)

    document = await document_of(resources, file_id)
    assert document is not None
    assert (document.status, document.error) == ("skipped", "The file has no text")
    assert document.sha256 == hashlib.sha256(content).hexdigest()


async def test_a_file_larger_than_the_limit_is_skipped(
    resources: Resources, library: FakeLibrary, consumer: EventConsumer, indexer: Indexer
) -> None:
    resources.settings.max_file_bytes = 20
    file_id = await publish(resources, library, "Library/Manuals/washer.md", MANUAL)
    await drain(consumer)
    await index_all(indexer)

    document = await document_of(resources, file_id)
    assert document is not None
    assert document.status == "skipped"
    assert document.error == "Files larger than 20 bytes are not indexed"


async def test_a_broken_file_fails_and_is_tried_again_with_back_off(
    resources: Resources, library: FakeLibrary, consumer: EventConsumer
) -> None:
    indexer = Indexer(resources, max_attempts=3, retry_seconds=30)
    file_id = await publish(resources, library, "Library/Manuals/broken.pdf", b"%PDF-1.4\nbroken\n")
    await drain(consumer)

    delays = []
    for attempt in (1, 2, 3):
        before = datetime.now(UTC)
        assert await index_all(indexer) == 1
        after = datetime.now(UTC)
        document = await document_of(resources, file_id)
        assert document is not None
        assert document.status == "failed"
        assert document.attempts == attempt
        assert document.error is not None
        assert document.error.startswith("Cannot read the file as pdf")
        if document.next_attempt_at is not None:
            assert before + timedelta(seconds=30 * 2 ** (attempt - 1)) <= document.next_attempt_at
            assert document.next_attempt_at <= after + timedelta(seconds=30 * 2 ** (attempt - 1))
            delays.append(attempt)
            # Not tried again before its time.
            assert await index_all(indexer) == 0
            await make_due(resources, file_id)
    # After the last attempt it waits for a re-index.
    assert delays == [1, 2]
    assert document.next_attempt_at is None


async def test_a_failure_of_nextcloud_is_tried_again_until_it_works(
    resources: Resources, library: FakeLibrary, consumer: EventConsumer, indexer: Indexer
) -> None:
    file_id = await publish(resources, library, "Library/Manuals/washer.md", MANUAL)
    await drain(consumer)
    library.status = 503

    await index_all(indexer)
    document = await document_of(resources, file_id)
    assert document is not None
    assert (document.status, document.attempts) == ("failed", 1)
    assert document.error == "GET Library/Manuals/washer.md: HTTP 503"

    library.status = None
    await make_due(resources, file_id)
    await index_all(indexer)
    document = await document_of(resources, file_id)
    assert document is not None
    assert (document.status, document.attempts, document.error) == ("indexed", 0, None)


async def test_a_file_no_longer_in_nextcloud_leaves_the_index(
    resources: Resources, library: FakeLibrary, consumer: EventConsumer, indexer: Indexer
) -> None:
    file_id = await publish(resources, library, "Library/Manuals/washer.md", MANUAL)
    await drain(consumer)
    del library.files["Library/Manuals/washer.md"]

    await index_all(indexer)

    assert await document_of(resources, file_id) is None


async def test_the_same_content_is_indexed_once(
    resources: Resources, library: FakeLibrary, consumer: EventConsumer, indexer: Indexer
) -> None:
    file_id = library.add("Library/Manuals/washer.md", MANUAL)
    data = published(file_id, "Library/Manuals/washer.md", MANUAL)
    # Delivered twice, then published again with the same content.
    await emit(resources, "file.published", data)
    await emit(resources, "file.published", data)
    await drain(consumer)
    await index_all(indexer)
    await emit(resources, "file.published", data)
    await drain(consumer)

    assert await index_all(indexer) == 0
    assert library.downloads == ["Library/Manuals/washer.md"]


async def test_new_content_replaces_the_passages_and_the_old_ones_stay_until_then(
    client: httpx.AsyncClient,
    resources: Resources,
    library: FakeLibrary,
    consumer: EventConsumer,
    indexer: Indexer,
) -> None:
    file_id = await publish(resources, library, "Library/Manuals/washer.md", MANUAL)
    await drain(consumer)
    await index_all(indexer)
    newer = b"The dishwasher manual: clean the filter every month.\n"
    library.files["Library/Manuals/washer.md"] = (file_id, newer)

    await emit(resources, "file.published", published(file_id, "Library/Manuals/washer.md", newer))
    await drain(consumer)

    queued = await document_of(resources, file_id)
    assert queued is not None
    assert queued.status == "indexed"
    assert queued.next_attempt_at is not None
    response = await client.get("/api/v1/search", params={"q": "washing machine"}, auth=ALICE)
    assert response.json()["results"][0]["document_id"] == queued.id

    await index_all(indexer)

    assert await passages_of(resources, queued.id) == [
        "The dishwasher manual: clean the filter every month."
    ]


async def test_a_renamed_file_keeps_its_passages_under_its_new_path(
    client: httpx.AsyncClient,
    resources: Resources,
    library: FakeLibrary,
    consumer: EventConsumer,
    indexer: Indexer,
) -> None:
    file_id = await publish(resources, library, "Library/Manuals/washer.md", MANUAL)
    await drain(consumer)
    await index_all(indexer)
    library.move("Library/Manuals/washer.md", "Library/Invoices/washer-2026.md")

    await emit(
        resources,
        "file.renamed",
        {
            "file_id": file_id,
            "path": "Library/Invoices/washer-2026.md",
            "old_path": "Library/Manuals/washer.md",
            "collection": "Invoices",
        },
    )
    await drain(consumer)

    document = await document_of(resources, file_id)
    assert document is not None
    assert (document.path, document.collection) == ("Library/Invoices/washer-2026.md", "Invoices")
    assert document.chunks == 1
    assert await index_all(indexer) == 0
    response = await client.get(
        "/api/v1/search", params={"q": "washing machine", "collection": "Invoices"}, auth=ALICE
    )
    assert [result["path"] for result in response.json()["results"]] == [
        "Library/Invoices/washer-2026.md"
    ]


async def test_a_removed_file_leaves_the_index(
    client: httpx.AsyncClient,
    resources: Resources,
    library: FakeLibrary,
    consumer: EventConsumer,
    indexer: Indexer,
) -> None:
    file_id = await publish(resources, library, "Library/Manuals/washer.md", MANUAL)
    await drain(consumer)
    await index_all(indexer)
    document = await document_of(resources, file_id)
    assert document is not None

    await emit(
        resources,
        "file.removed",
        {"file_id": file_id, "path": "Library/Manuals/washer.md", "submitted_by": "alice"},
    )
    await drain(consumer)

    assert await document_of(resources, file_id) is None
    assert await passages_of(resources, document.id) == []
    response = await client.get("/api/v1/search", params={"q": "washing machine"}, auth=ALICE)
    assert response.json()["results"] == []


@pytest.mark.parametrize(
    ("event_type", "data"),
    [
        ("file.published", {"file_id": 4182, "path": "Library/Manuals/a.md"}),
        ("file.published", {"file_id": "x", "path": "a", "collection": "a", "sha256": "0" * 64}),
        ("file.renamed", {"file_id": 4182}),
        ("file.removed", {"path": "Library/Manuals/a.md"}),
        ("file.removed", {"file_id": 2**63}),
    ],
)
async def test_events_without_the_data_they_need_are_skipped(
    resources: Resources, consumer: EventConsumer, event_type: str, data: dict[str, object]
) -> None:
    await emit(resources, event_type, data)

    assert await drain(consumer) == 1

    settings = resources.settings
    info = await resources.redis.xpending(settings.events_stream, settings.events_group)
    assert info["pending"] == 0
    assert not resources.indexer_wakeup.is_set()


async def test_other_events_and_broken_entries_are_acknowledged(
    resources: Resources, consumer: EventConsumer
) -> None:
    stream = resources.settings.events_stream
    await emit(resources, "search.performed", {"k": 5, "collection": None, "results": 0})
    await resources.redis.xadd(stream, {"event": "not json"})
    await resources.redis.xadd(stream, {"other": "field"})

    assert await drain(consumer) == 3

    info = await resources.redis.xpending(stream, resources.settings.events_group)
    assert info["pending"] == 0


async def test_an_idle_read_ends_before_the_client_times_out(consumer: EventConsumer) -> None:
    # The Redis client gives up on a read after its socket timeout (5 s).
    await consumer.create_group()

    assert await consumer.consume(BLOCK_MS) == 0


async def test_published_files_are_indexed_while_the_tasks_run(
    resources: Resources, library: FakeLibrary, mocked: respx.MockRouter
) -> None:
    tasks = [
        asyncio.create_task(EventConsumer(resources, name="runner", block_ms=100).run()),
        asyncio.create_task(Indexer(resources, poll_seconds=0.05).run()),
    ]
    try:
        file_id = await publish(resources, library, "Library/Manuals/washer.md", MANUAL)
        for _ in range(200):
            document = await document_of(resources, file_id)
            if document is not None and document.status == "indexed":
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("the file was not indexed")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
