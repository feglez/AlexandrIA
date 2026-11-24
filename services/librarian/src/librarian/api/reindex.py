"""Re-index jobs (README section 5.3).

A curator asks for a re-index of the whole library or of one collection.
The job waits in the database until an indexer starts it (librarian.indexer),
so the answer is 202 with the job, which can be followed with its id.
"""

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from ulid import ULID

from librarian.api.documents import CollectionName
from librarian.auth import CurrentCurator
from librarian.db.models import Document, ReindexJob
from librarian.db.session import DbSession
from librarian.problems import Problem
from librarian.resources import Resources

router = APIRouter(prefix="/api/v1", tags=["reindex"])

NOT_FOUND = "There is no such re-index job."


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Only this collection; the whole library without it.
    collection: CollectionName | None = None


class JobOut(BaseModel):
    job_id: str
    status: str
    collection: str | None
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # Documents the job queued, once it has started, and how many of them
    # still wait for the indexer.
    documents: int | None
    pending: int
    error: str | None


async def job_out(session: DbSession, job: ReindexJob) -> JobOut:
    pending = await session.scalar(
        select(func.count()).select_from(Document).where(Document.job_id == job.id)
    )
    return JobOut(
        job_id=job.id,
        status=job.status,
        collection=job.collection,
        requested_by=job.requested_by,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        documents=job.documents,
        pending=pending or 0,
        error=job.error,
    )


@router.post("/reindex", status_code=202)
async def start_reindex(
    request: Request,
    curator: CurrentCurator,
    session: DbSession,
    body: ReindexRequest | None = None,
) -> JobOut:
    """Index every document of the library, or of one collection, again."""
    resources: Resources = request.app.state.resources
    job = ReindexJob(
        id=f"job_{ULID()}",
        collection=body.collection if body else None,
        status="queued",
        requested_by=curator.id,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    resources.indexer_wakeup.set()
    return await job_out(session, job)


@router.get("/reindex/{job_id}", responses={404: {"description": "No such re-index job"}})
async def get_reindex(job_id: str, curator: CurrentCurator, session: DbSession) -> JobOut:
    """How far the job has got."""
    job = await session.get(ReindexJob, job_id)
    if job is None:
        raise Problem(404, NOT_FOUND)
    return await job_out(session, job)
