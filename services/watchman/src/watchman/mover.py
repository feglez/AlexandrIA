"""Mover: publishes approved submissions in the library.

Every watchman process runs one mover as an asyncio task. It takes one
approved submission at a time with SELECT ... FOR UPDATE SKIP LOCKED, so two
replicas never take the same one, and sets `next_attempt_at` a few minutes
ahead before it calls Nextcloud: no other mover touches the submission until
then, and if this process dies the submission is taken again after that time.

For each submission it moves the quarantined file into
Library/<collection>/ with WebDAV MOVE (never overwriting: a taken name gets
" (2)", " (3)", ...), records it in `library_files`, sets the status to
`published` and emits `file.published`. A failed attempt is retried with
back-off; after MAX_ATTEMPTS the submission is `failed`.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from redis.exceptions import RedisError
from sqlalchemy import delete, or_, select, update

from watchman.db.models import LibraryFile, StatusChange, Submission
from watchman.files import FilesError
from watchman.lifecycle import check_transition
from watchman.resources import Resources

logger = logging.getLogger(__name__)

# Actor of the status changes made by the mover itself.
ACTOR = "watchman"
MAX_ATTEMPTS = 5
# Wait before the first retry; it doubles after each failed attempt.
RETRY_SECONDS = 10.0
# Longer than one attempt can take (a few Nextcloud calls of at most 10 s).
LEASE = timedelta(minutes=5)
POLL_SECONDS = 2.0
MAX_NAME_NUMBER = 20


def target_names(filename: str) -> Iterator[str]:
    """`report.pdf`, then `report (2).pdf`, `report (3).pdf`, ..."""
    yield filename
    name = PurePosixPath(filename)
    for number in range(2, MAX_NAME_NUMBER + 1):
        yield f"{name.stem} ({number}){name.suffix}"


@dataclass(frozen=True)
class Claim:
    id: str
    filename: str
    collection: str
    quarantine_path: str | None
    library_path: str | None
    file_id: int | None
    attempt: int


class Mover:
    def __init__(
        self,
        resources: Resources,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        retry_seconds: float = RETRY_SECONDS,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._resources = resources
        self._max_attempts = max_attempts
        self._retry_seconds = retry_seconds
        self._poll_seconds = poll_seconds

    async def run(self) -> None:
        """Publish due submissions until cancelled.

        It looks again every few seconds, or at once when an approval sets
        `resources.mover_wakeup`.
        """
        wakeup = self._resources.mover_wakeup
        while True:
            wakeup.clear()
            try:
                while await self.publish_next():
                    pass
            except Exception:
                logger.exception("The mover failed, it tries again shortly")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wakeup.wait(), self._poll_seconds)

    async def publish_next(self) -> bool:
        """Make one attempt at the next due submission. False if none is due."""
        claim = await self._claim()
        if claim is None:
            return False
        try:
            path, file_id = await self._move(claim)
        except FilesError as exc:
            await self._attempt_failed(claim, str(exc))
        else:
            await self._published(claim, path, file_id)
        return True

    async def _claim(self) -> Claim | None:
        now = datetime.now(UTC)
        async with self._resources.sessions.begin() as session:
            submission = await session.scalar(
                select(Submission)
                .where(Submission.status == "approved", Submission.next_attempt_at <= now)
                .order_by(Submission.next_attempt_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if submission is None:
                return None
            submission.publish_attempts += 1
            submission.next_attempt_at = now + LEASE
            return Claim(
                id=submission.id,
                filename=submission.filename,
                collection=submission.collection,
                quarantine_path=submission.quarantine_path,
                library_path=submission.library_path,
                file_id=submission.file_id,
                attempt=submission.publish_attempts,
            )

    async def _move(self, claim: Claim) -> tuple[str, int]:
        """Move the file into its collection; returns its path and file id."""
        files = self._resources.files
        source_id = await files.file_id(claim.quarantine_path) if claim.quarantine_path else None
        if source_id is None:
            # An earlier attempt may have moved the file and stopped before
            # it could record that: look where it was going.
            if (
                claim.library_path
                and claim.file_id is not None
                and await files.file_id(claim.library_path) == claim.file_id
            ):
                return claim.library_path, claim.file_id
            raise FilesError(f"{claim.quarantine_path} is not in the quarantine")

        folder = f"{self._resources.settings.library_folder}/{claim.collection}"
        for name in target_names(claim.filename):
            target = f"{folder}/{name}"
            await self._record_target(claim.id, target, source_id)
            if await files.move(claim.quarantine_path, target):
                return target, source_id
        raise FilesError(f"No free name for {claim.filename} in {folder}")

    async def _record_target(self, submission_id: str, target: str, file_id: int) -> None:
        async with self._resources.sessions.begin() as session:
            await session.execute(
                update(Submission)
                .where(Submission.id == submission_id)
                .values(library_path=target, file_id=file_id)
            )

    async def _attempt_failed(self, claim: Claim, error: str) -> None:
        now = datetime.now(UTC)
        async with self._resources.sessions.begin() as session:
            submission = await session.get(Submission, claim.id, with_for_update=True)
            if submission is None or submission.status != "approved":
                return
            submission.last_error = error
            if claim.attempt >= self._max_attempts:
                check_transition(submission.status, "failed")
                submission.status = "failed"
                submission.next_attempt_at = None
                submission.updated_at = now
                session.add(
                    StatusChange(
                        submission_id=submission.id,
                        from_status="approved",
                        to_status="failed",
                        actor=ACTOR,
                        reason=error,
                    )
                )
            else:
                delay = self._retry_seconds * 2 ** (claim.attempt - 1)
                submission.next_attempt_at = now + timedelta(seconds=delay)
        logger.warning("Attempt %s to publish %s failed: %s", claim.attempt, claim.id, error)

    async def _published(self, claim: Claim, path: str, file_id: int) -> None:
        resources = self._resources
        async with resources.sessions.begin() as session:
            submission = await session.get(Submission, claim.id, with_for_update=True)
            if submission is None or submission.status != "approved":
                logger.warning("%s changed while it was being published", claim.id)
                return
            check_transition(submission.status, "published")
            curator = await session.scalar(
                select(StatusChange.actor)
                .where(
                    StatusChange.submission_id == submission.id,
                    StatusChange.to_status == "approved",
                )
                .order_by(StatusChange.id.desc())
                .limit(1)
            )
            submission.status = "published"
            submission.library_path = path
            submission.file_id = file_id
            submission.quarantine_path = None
            submission.next_attempt_at = None
            submission.last_error = None
            submission.updated_at = datetime.now(UTC)
            # Files deleted in Nextcloud are only forgotten here once the
            # webhooks exist, so an old row may still hold this path or id.
            await session.execute(
                delete(LibraryFile).where(
                    or_(LibraryFile.path == path, LibraryFile.file_id == file_id)
                )
            )
            session.add(
                LibraryFile(
                    file_id=file_id,
                    path=path,
                    collection=submission.collection,
                    sha256=submission.sha256,
                    size_bytes=submission.size_bytes,
                    submitted_by=submission.submitted_by,
                    submission_id=submission.id,
                )
            )
            session.add(
                StatusChange(
                    submission_id=submission.id,
                    from_status="approved",
                    to_status="published",
                    actor=ACTOR,
                )
            )
            data = {
                "submission_id": submission.id,
                "path": path,
                "collection": submission.collection,
                "file_id": file_id,
                "size_bytes": submission.size_bytes,
                "mime_type": submission.mime_type,
                "sha256": submission.sha256,
                "submitted_by": submission.submitted_by,
            }
        logger.info("Published %s as %s", claim.id, path)

        try:
            await resources.events.publish("file.published", curator or ACTOR, data)
        except RedisError as exc:
            logger.error("Cannot publish file.published for %s: %s", claim.id, exc)
        # The folder is empty now; a folder left behind does no harm.
        folder = f"{resources.settings.quarantine_folder}/{claim.id}"
        try:
            await resources.files.delete(folder)
        except FilesError as exc:
            logger.warning("Cannot delete %s: %s", folder, exc)
