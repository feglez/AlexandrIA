"""The reconciliation of the index with the files of the library."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from librarian.db.models import Document
from librarian.indexer import Indexer
from librarian.library import LibraryError
from librarian.reconcile import Reconciler, reconcile
from librarian.resources import Resources
from tests.integration.conftest import FakeLibrary, document_of, index, index_all


async def test_files_missing_from_the_index_are_queued_and_indexed(
    resources: Resources, library: FakeLibrary, indexer: Indexer
) -> None:
    file_id = library.add("Library/Manuals/Old ones/washer.md", b"Washing machine warranty.")
    library.add("Library/loose.md", b"Not in a collection.")

    changes = await reconcile(resources)

    assert (changes.added, changes.moved, changes.removed) == (1, 0, 0)
    assert changes.collections == {"Manuals", "Invoices", "School"}
    assert resources.indexer_wakeup.is_set()
    document = await document_of(resources, file_id)
    assert document is not None
    assert (document.status, document.collection, document.sha256) == ("pending", "Manuals", None)
    await index_all(indexer)
    document = await document_of(resources, file_id)
    assert document is not None
    assert document.status == "indexed"
    assert document.sha256 is not None


async def test_documents_whose_file_is_gone_are_removed(
    resources: Resources, library: FakeLibrary
) -> None:
    gone = await index(resources, "Library/Manuals/gone.md", "Gone.")
    kept = await index(resources, "Library/Manuals/kept.md", "Kept.")
    library.files["Library/Manuals/kept.md"] = (kept.file_id, b"Kept.")

    changes = await reconcile(resources)

    assert (changes.added, changes.moved, changes.removed) == (0, 0, 1)
    assert await document_of(resources, gone.file_id) is None
    assert await document_of(resources, kept.file_id) is not None


async def test_moved_files_get_their_new_path_and_keep_their_passages(
    resources: Resources, library: FakeLibrary
) -> None:
    moved = await index(resources, "Library/Manuals/washer.md", "Washing machine.")
    library.files["Library/Invoices/washer.md"] = (moved.file_id, b"Washing machine.")

    changes = await reconcile(resources)

    assert (changes.added, changes.moved, changes.removed) == (0, 1, 0)
    document = await document_of(resources, moved.file_id)
    assert document is not None
    assert (document.path, document.collection) == ("Library/Invoices/washer.md", "Invoices")
    assert (document.status, document.chunks) == ("indexed", 1)


async def test_documents_changed_after_the_listing_started_are_left_alone(
    resources: Resources, library: FakeLibrary
) -> None:
    # An event published the first one and moved the second one while the
    # library was being listed, so the listing does not show the change yet.
    new = await index(resources, "Library/Manuals/new.md", "New.")
    moved = await index(resources, "Library/Invoices/moved.md", "Moved.")
    library.files["Library/Manuals/moved.md"] = (moved.file_id, b"Moved.")
    async with resources.sessions.begin() as session:
        await session.execute(
            update(Document).values(updated_at=datetime.now(UTC) + timedelta(minutes=1))
        )

    await reconcile(resources)

    assert await document_of(resources, new.file_id) is not None
    document = await document_of(resources, moved.file_id)
    assert document is not None
    assert document.path == "Library/Invoices/moved.md"


async def test_nothing_changes_when_the_library_cannot_be_listed(
    resources: Resources, library: FakeLibrary
) -> None:
    document = await index(resources, "Library/Manuals/washer.md", "Washing machine.")
    library.status = 503

    with pytest.raises(LibraryError, match="HTTP 503"):
        await reconcile(resources)

    assert await document_of(resources, document.file_id) is not None


async def test_the_reconciler_runs_at_start_and_tries_again_after_a_failure(
    resources: Resources, library: FakeLibrary
) -> None:
    library.status = 503
    file_id = library.add("Library/Manuals/washer.md", b"Washing machine.")
    task = asyncio.create_task(
        Reconciler(resources, interval_seconds=3600, retry_seconds=0.05).run()
    )
    try:
        await asyncio.sleep(0.2)
        assert await document_of(resources, file_id) is None
        library.status = None
        for _ in range(100):
            if await document_of(resources, file_id) is not None:
                break
            await asyncio.sleep(0.05)
        else:
            pytest.fail("the reconciler did not try again")
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
