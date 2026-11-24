"""Indexer: puts the documents that are due into the search index.

Every librarian process runs one indexer as an asyncio task. It takes one
due document at a time (`next_attempt_at` in the past) with SELECT ... FOR
UPDATE SKIP LOCKED, so two replicas never take the same one, and moves
`next_attempt_at` a few minutes ahead (a lease) before it reads the file: no
other indexer takes the document until then, and if this process dies the
document is taken again after that time.

For each document it downloads the file from Nextcloud, extracts the text,
cuts it into passages, embeds them and replaces the document's passages:
the document is then `indexed`. A file of a type that is not indexed, a
file that is too large and a file without text are `skipped`. A failed
attempt makes the document `failed`, and it is tried again with back-off;
after MAX_ATTEMPTS only a re-index job tries it again. A file that is no
longer in Nextcloud is removed from the index.

The indexer also starts the re-index jobs that curators ask for: it brings
the index in step with the library (librarian.reconcile) and queues every
document of the job's collection, or of the whole library.
"""

import asyncio
import contextlib
import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, select, update

from librarian import chunking, reconcile
from librarian.db.models import Chunk, Document, ReindexJob
from librarian.extract import extract, kind_of
from librarian.library import TooLarge
from librarian.resources import Resources

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
# Wait before the first retry; it doubles after each failed attempt.
RETRY_SECONDS = 30.0
# Longer than one attempt can take: embedding a long file on a small
# machine takes minutes.
LEASE = timedelta(minutes=10)
POLL_SECONDS = 2.0
# Passages embedded in one call. Searches wait for the embedder, so a long
# file must not keep it busy for long at a time.
BATCH = 16
SUPPORTED = "Only PDF, DOCX, Markdown and plain text files are indexed"


@dataclass(frozen=True)
class Claim:
    id: str
    path: str
    lease: datetime


@dataclass(frozen=True)
class Indexed:
    sha256: str
    passages: list[str]
    embeddings: list[list[float]]


@dataclass(frozen=True)
class Skipped:
    reason: str
    sha256: str | None = None


class Gone:
    """The file is no longer in Nextcloud."""


class Indexer:
    def __init__(
        self,
        resources: Resources,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        retry_seconds: float = RETRY_SECONDS,
        poll_seconds: float = POLL_SECONDS,
        batch: int = BATCH,
    ) -> None:
        self._resources = resources
        self._max_attempts = max_attempts
        self._retry_seconds = retry_seconds
        self._poll_seconds = poll_seconds
        self._batch = batch

    async def run(self) -> None:
        """Index due documents until cancelled.

        It looks again every few seconds, or at once when a new event or job
        sets `resources.indexer_wakeup`.
        """
        wakeup = self._resources.indexer_wakeup
        while True:
            wakeup.clear()
            try:
                while await self.start_next_job():
                    pass
                while await self.index_next():
                    pass
                await self.finish_jobs()
            except Exception:
                logger.exception("The indexer failed, it tries again shortly")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wakeup.wait(), self._poll_seconds)

    async def index_next(self) -> bool:
        """Make one attempt at the next due document. False if none is due."""
        claim = await self._claim()
        if claim is None:
            return False
        try:
            result = await self._read(claim)
        except Exception as exc:
            await self._failed(claim, str(exc) or type(exc).__name__)
        else:
            await self._store(claim, result)
        return True

    async def _claim(self) -> Claim | None:
        now = datetime.now(UTC)
        async with self._resources.sessions.begin() as session:
            document = await session.scalar(
                select(Document)
                .where(Document.next_attempt_at <= now)
                .order_by(Document.next_attempt_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if document is None:
                return None
            document.next_attempt_at = now + LEASE
            return Claim(id=document.id, path=document.path, lease=document.next_attempt_at)

    async def _read(self, claim: Claim) -> Indexed | Skipped | Gone:
        """Download, extract, cut and embed the file."""
        kind = kind_of(claim.path)
        if kind is None:
            return Skipped(SUPPORTED)
        resources = self._resources
        try:
            content = await resources.library.download(
                claim.path, resources.settings.max_file_bytes
            )
        except TooLarge:
            return Skipped(
                f"Files larger than {resources.settings.max_file_bytes} bytes are not indexed"
            )
        if content is None:
            return Gone()
        sha256 = hashlib.sha256(content).hexdigest()
        # Reading a PDF or a DOCX is CPU-bound: not in the event loop.
        passages = await asyncio.to_thread(lambda: chunking.passages(extract(kind, content)))
        if not passages:
            return Skipped("The file has no text", sha256)
        embeddings = []
        for start in range(0, len(passages), self._batch):
            embeddings += await resources.embedder.passages(passages[start : start + self._batch])
        return Indexed(sha256, passages, embeddings)

    async def _store(self, claim: Claim, result: Indexed | Skipped | Gone) -> None:
        now = datetime.now(UTC)
        async with self._resources.sessions.begin() as session:
            document = await session.get(Document, claim.id, with_for_update=True)
            if document is None or document.next_attempt_at != claim.lease:
                # Removed, or queued again (new content, a re-index) while
                # this attempt ran: the next attempt stores the new state.
                return
            if isinstance(result, Gone):
                if document.path == claim.path:
                    await session.delete(document)
                    logger.info("Removed %s from the index: it is not in Nextcloud", claim.path)
                else:
                    # Moved while it was being read: read it at its new path.
                    document.next_attempt_at = now
                return
            await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
            if isinstance(result, Indexed):
                session.add_all(
                    Chunk(document_id=document.id, ord=number, text=passage, embedding=embedding)
                    for number, (passage, embedding) in enumerate(
                        zip(result.passages, result.embeddings, strict=True)
                    )
                )
                document.status = "indexed"
                document.chunks = len(result.passages)
                document.indexed_at = now
                document.sha256 = result.sha256
                document.error = None
            else:
                document.status = "skipped"
                document.chunks = 0
                document.indexed_at = None
                document.sha256 = result.sha256 or document.sha256
                document.error = result.reason
            document.attempts = 0
            document.next_attempt_at = None
            document.job_id = None
        logger.info("Indexed %s: %s", claim.path, document.status)

    async def _failed(self, claim: Claim, error: str) -> None:
        now = datetime.now(UTC)
        async with self._resources.sessions.begin() as session:
            document = await session.get(Document, claim.id, with_for_update=True)
            if document is None or document.next_attempt_at != claim.lease:
                return
            document.status = "failed"
            document.error = error
            document.attempts += 1
            document.job_id = None
            if document.attempts < self._max_attempts:
                delay = self._retry_seconds * 2 ** (document.attempts - 1)
                document.next_attempt_at = now + timedelta(seconds=delay)
            else:
                document.next_attempt_at = None
            attempt = document.attempts
        logger.warning("Attempt %s to index %s failed: %s", attempt, claim.path, error)

    async def start_next_job(self) -> bool:
        """Start the oldest queued re-index job. False if there is none."""
        now = datetime.now(UTC)
        async with self._resources.sessions.begin() as session:
            job = await session.scalar(
                select(ReindexJob)
                .where(ReindexJob.status == "queued")
                .order_by(ReindexJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return False
            job.status = "running"
            job.started_at = now
            job_id, collection = job.id, job.collection

        try:
            changes = await reconcile.reconcile(self._resources)
        except Exception as exc:
            await self._job_failed(job_id, f"Cannot list the library: {exc}")
            return True
        if collection is not None and collection not in changes.collections:
            await self._job_failed(job_id, f"There is no collection {collection} in the library")
            return True

        async with self._resources.sessions.begin() as session:
            queued = update(Document).values(next_attempt_at=now, attempts=0, job_id=job_id)
            if collection is not None:
                queued = queued.where(Document.collection == collection)
            result = await session.execute(queued)
            await session.execute(
                update(ReindexJob).where(ReindexJob.id == job_id).values(documents=result.rowcount)
            )
        logger.info("Re-index job %s queued %s documents", job_id, result.rowcount)
        return True

    async def _job_failed(self, job_id: str, error: str) -> None:
        async with self._resources.sessions.begin() as session:
            await session.execute(
                update(ReindexJob)
                .where(ReindexJob.id == job_id)
                .values(status="failed", error=error, finished_at=datetime.now(UTC))
            )
        logger.warning("Re-index job %s failed: %s", job_id, error)

    async def finish_jobs(self) -> None:
        """Mark done the running jobs whose documents have all been tried."""
        now = datetime.now(UTC)
        async with self._resources.sessions.begin() as session:
            await session.execute(
                update(ReindexJob)
                .where(
                    ReindexJob.status == "running",
                    ReindexJob.documents.is_not(None),
                    ~exists().where(Document.job_id == ReindexJob.id),
                )
                .values(status="done", finished_at=now)
            )
            # A process that stopped while it started a job left it running.
            await session.execute(
                update(ReindexJob)
                .where(
                    ReindexJob.status == "running",
                    ReindexJob.documents.is_(None),
                    ReindexJob.started_at < now - LEASE,
                )
                .values(
                    status="failed",
                    error="librarian stopped while it was starting the job",
                    finished_at=now,
                )
            )
