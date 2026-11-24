"""The documents of the search index, as the domain events change them.

watchman emits file.published when a file reaches the library, and
file.renamed and file.removed when Nextcloud tells it that a file of the
library was moved or deleted (README section 4.3). Publishing queues the
document for the indexer (librarian.indexer), unless the same content is
already indexed; moving it only changes its path and collection; removing it
deletes the document and its passages.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from librarian.db.models import Document


def new_document_id() -> str:
    return f"doc_{ULID()}"


# Nextcloud file ids fit in a bigint column.
FileId = Annotated[int, Field(gt=0, lt=2**63)]


class _Published(BaseModel):
    file_id: FileId
    path: str = Field(min_length=1)
    collection: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _Renamed(BaseModel):
    file_id: FileId
    path: str = Field(min_length=1)
    collection: str = Field(min_length=1)


class _Removed(BaseModel):
    file_id: FileId


async def handle(session: AsyncSession, event_type: str, data: dict[str, Any]) -> bool:
    """Apply one domain event to the index; other event types change nothing.

    Returns True if a document now waits for the indexer. Raises ValueError
    if the event does not have the data it needs.
    """
    if event_type == "file.published":
        published = _Published.model_validate(data)
        return await queue(
            session, published.file_id, published.path, published.collection, published.sha256
        )
    if event_type == "file.renamed":
        renamed = _Renamed.model_validate(data)
        await session.execute(
            update(Document)
            .where(Document.file_id == renamed.file_id)
            .values(path=renamed.path, collection=renamed.collection, updated_at=datetime.now(UTC))
        )
    elif event_type == "file.removed":
        removed = _Removed.model_validate(data)
        # The passages go with it (ON DELETE CASCADE).
        await session.execute(delete(Document).where(Document.file_id == removed.file_id))
    return False


async def queue(
    session: AsyncSession, file_id: int, path: str, collection: str, sha256: str | None
) -> bool:
    """Record a file of the library and queue it for indexing, unless the
    same content is already indexed (or skipped). True if it was queued."""
    now = datetime.now(UTC)
    inserted = await session.scalar(
        insert(Document)
        .values(
            id=new_document_id(),
            file_id=file_id,
            path=path,
            collection=collection,
            sha256=sha256,
            status="pending",
            next_attempt_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(index_elements=[Document.file_id])
        .returning(Document.id)
    )
    if inserted is not None:
        return True
    document = await session.scalar(
        select(Document).where(Document.file_id == file_id).with_for_update()
    )
    if document is None:  # removed in the meantime
        return False
    document.path = path
    document.collection = collection
    document.updated_at = now
    if (
        sha256 is not None
        and document.sha256 == sha256
        and document.status in ("indexed", "skipped")
    ):
        return False
    if sha256 is not None:
        document.sha256 = sha256
    document.attempts = 0
    document.next_attempt_at = now
    return True
