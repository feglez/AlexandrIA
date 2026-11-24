"""Files of the library moved or deleted in Nextcloud.

Nextcloud calls watchman's internal webhook (watchman.api.webhooks) when a
file or folder of the service account is moved, renamed or deleted, also
when watchman itself moves a file from the quarantine or deletes one there.
Only the files of the library that watchman published count (the
`library_files` table), and a folder counts for every one of them inside it:

- moved within the library: the file keeps its id and gets its new path
  and collection, and watchman emits file.renamed;
- deleted, or moved out of the library: the file is forgotten, and watchman
  emits file.removed with the member who submitted it and its size, so that
  observer takes it from the member's contribution (README section 4.3).

Nextcloud tells the path of a deleted node, not its id, and the old path
and the new path and id of a moved one.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from watchman.db.models import LibraryFile


@dataclass(frozen=True)
class Change:
    """A domain event to emit once the change is committed."""

    type: str
    data: dict[str, Any]


class Library:
    """Paths of Nextcloud's webhooks seen from the service account's home."""

    def __init__(self, user: str, folder: str) -> None:
        self._prefix = f"/{user}/files/"
        self._folder = folder.strip("/")

    def path(self, nextcloud_path: str) -> str | None:
        """The path in the service account's home of a node of the library, for
        example "Library/Manuals/a.pdf" for "/alexandria/files/Library/Manuals/a.pdf".
        None for a node outside the library."""
        if not nextcloud_path.startswith(self._prefix):
            return None
        path = nextcloud_path.removeprefix(self._prefix).strip("/")
        return path if path.split("/")[0] == self._folder else None


def collection_of(path: str) -> str | None:
    """The collection of a file of the library, or None for a path that is not
    inside a collection folder ("Library/a.pdf")."""
    parts = path.split("/")
    return parts[1] if len(parts) > 2 else None


async def _files_under(session: AsyncSession, path: str) -> list[LibraryFile]:
    """The published files at `path` or inside it, locked."""
    rows = await session.scalars(
        select(LibraryFile)
        .where(
            or_(LibraryFile.path == path, LibraryFile.path.startswith(f"{path}/", autoescape=True))
        )
        .order_by(LibraryFile.path)
        .with_for_update()
    )
    return list(rows)


def _data(row: LibraryFile) -> dict[str, Any]:
    return {
        "file_id": row.file_id,
        "path": row.path,
        "collection": row.collection,
        "size_bytes": row.size_bytes,
        "submitted_by": row.submitted_by,
        "submission_id": row.submission_id,
    }


async def deleted(session: AsyncSession, path: str) -> list[Change]:
    """A node of the library was deleted."""
    rows = await _files_under(session, path)
    for row in rows:
        await session.delete(row)
    return [Change("file.removed", _data(row)) for row in rows]


async def moved(session: AsyncSession, source: str, target: str | None) -> list[Change]:
    """A node of the library was moved to `target` (None: out of the library)."""
    rows = await _files_under(session, source)
    changes = []
    for row in rows:
        new_path = target + row.path[len(source) :] if target is not None else None
        collection = collection_of(new_path) if new_path is not None else None
        if new_path is None or collection is None:
            await session.delete(row)
            changes.append(Change("file.removed", _data(row)))
            continue
        # A file overwritten by the move is no longer in the library.
        replaced = await session.scalar(
            select(LibraryFile)
            .where(LibraryFile.path == new_path, LibraryFile.file_id != row.file_id)
            .with_for_update()
        )
        if replaced is not None and replaced not in rows:
            await session.execute(
                delete(LibraryFile).where(LibraryFile.file_id == replaced.file_id)
            )
            changes.append(Change("file.removed", _data(replaced)))
        old_path = row.path
        row.path = new_path
        row.collection = collection
        await session.flush()
        changes.append(Change("file.renamed", _data(row) | {"old_path": old_path}))
    return changes
