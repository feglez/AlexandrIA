"""Reconciliation: brings the index in step with the files of the library.

The events keep the index up to date, but Nextcloud does not call a webhook
again when watchman did not get it, and files can reach the library without
watchman (an administrator copying them in). So every librarian process
also lists the library now and then (one PROPFIND), and then:

- queues the files that the index does not know;
- removes the documents whose file is gone;
- updates the path and collection of the files that were moved.

Documents that an event changed after the listing started are left alone:
the listing may be older than the event.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from librarian.db.models import Document
from librarian.index import new_document_id
from librarian.library import collection_of
from librarian.resources import Resources

logger = logging.getLogger(__name__)

# Rows in one INSERT, well below the limit of query parameters.
INSERT_BATCH = 1000


@dataclass(frozen=True)
class Changes:
    added: int
    moved: int
    removed: int
    # Collections of the library, as listed.
    collections: frozenset[str]


async def reconcile(resources: Resources) -> Changes:
    started = datetime.now(UTC)
    listing = await resources.library.listing()
    files = listing.files
    async with resources.sessions.begin() as session:
        rows = await session.execute(select(Document.file_id, Document.path, Document.updated_at))
        known = set()
        gone = []
        moved = []
        for file_id, path, updated_at in rows:
            known.add(file_id)
            if updated_at >= started:
                continue
            if file_id not in files:
                gone.append(file_id)
            elif files[file_id] != path:
                moved.append(file_id)
        if gone:
            await session.execute(
                delete(Document).where(Document.file_id.in_(gone), Document.updated_at < started)
            )
        for file_id in moved:
            path = files[file_id]
            await session.execute(
                update(Document)
                .where(Document.file_id == file_id, Document.updated_at < started)
                .values(path=path, collection=collection_of(path), updated_at=started)
            )
        new = [(file_id, path) for file_id, path in files.items() if file_id not in known]
        now = datetime.now(UTC)
        for start in range(0, len(new), INSERT_BATCH):
            await session.execute(
                insert(Document)
                .values(
                    [
                        {
                            "id": new_document_id(),
                            "file_id": file_id,
                            "path": path,
                            "collection": collection_of(path),
                            "status": "pending",
                            "next_attempt_at": now,
                            "updated_at": now,
                        }
                        for file_id, path in new[start : start + INSERT_BATCH]
                    ]
                )
                .on_conflict_do_nothing(index_elements=[Document.file_id])
            )
    if new:
        resources.indexer_wakeup.set()
    return Changes(
        added=len(new), moved=len(moved), removed=len(gone), collections=listing.collections
    )


class Reconciler:
    """Reconciles once when the service starts, then every `interval_seconds`."""

    def __init__(
        self,
        resources: Resources,
        *,
        interval_seconds: float | None = None,
        retry_seconds: float | None = None,
    ) -> None:
        settings = resources.settings
        self._resources = resources
        self._interval = interval_seconds or settings.reconcile_seconds
        self._retry = retry_seconds or settings.reconcile_retry_seconds

    async def run(self) -> None:
        while True:
            try:
                changes = await reconcile(self._resources)
            except Exception as exc:
                logger.warning("Cannot compare the index with the library: %s", exc)
                delay = self._retry
            else:
                if changes.added or changes.moved or changes.removed:
                    logger.info(
                        "Reconciled the index with the library: %d added, %d moved, %d removed",
                        changes.added,
                        changes.moved,
                        changes.removed,
                    )
                delay = self._interval
            await asyncio.sleep(delay)
