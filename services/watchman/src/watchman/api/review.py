"""Curator decisions on submissions, and the list of submissions.

GET  /api/v1/submissions                 members see their own, curators all
POST /api/v1/submissions/{id}/approve    curators; the mover publishes the file
POST /api/v1/submissions/{id}/reject     curators; the quarantined file is deleted

Every decision is stored in the status history with the curator and the time,
and published as `submission.decided`.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, StringConstraints
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from watchman.api.submissions import SubmissionOut
from watchman.auth import CurrentCurator, CurrentUser, User
from watchman.db.models import SUBMISSION_STATUSES, StatusChange, Submission
from watchman.db.session import DbSession
from watchman.files import FilesError, NextcloudFiles
from watchman.lifecycle import can_change
from watchman.problems import Problem
from watchman.resources import Resources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["submissions"])

MAX_REASON_LENGTH = 1000
# Pauses between the attempts to delete a quarantined file.
DELETE_RETRY_DELAYS = (0.5, 1.0)

Status = Literal[SUBMISSION_STATUSES]
Reason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_REASON_LENGTH)
]

DECISION_ERRORS = {
    403: {"description": "The user is not a curator"},
    404: {"description": "No such submission"},
    409: {"description": "The submission's status does not allow this decision"},
}


class SubmissionPage(BaseModel):
    items: list[SubmissionOut]
    total: int
    limit: int
    offset: int


class RejectRequest(BaseModel):
    reason: Reason


@router.get("/submissions")
async def list_submissions(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SubmissionPage:
    """Newest first. Members only see their own submissions."""
    resources: Resources = request.app.state.resources
    query = select(Submission)
    if resources.settings.curators_group not in user.groups:
        query = query.where(Submission.submitted_by == user.id)
    if status is not None:
        query = query.where(Submission.status == status)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(
        query.order_by(Submission.created_at.desc(), Submission.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return SubmissionPage(
        items=[SubmissionOut.model_validate(row, from_attributes=True) for row in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.post("/submissions/{submission_id}/approve", responses=DECISION_ERRORS)
async def approve(
    request: Request, submission_id: str, curator: CurrentCurator, session: DbSession
) -> SubmissionOut:
    """Approve a pending submission, or a failed one to retry its publication."""
    resources: Resources = request.app.state.resources
    submission = await _locked(session, submission_id)
    previous = _change(submission, "approved")
    # Due at once, with a fresh count of attempts.
    submission.publish_attempts = 0
    submission.next_attempt_at = submission.updated_at
    submission.last_error = None
    _record(session, submission, previous, curator)
    await session.commit()

    resources.mover_wakeup.set()
    await _publish_decided(resources, submission, curator, None)
    return SubmissionOut.model_validate(submission, from_attributes=True)


@router.post(
    "/submissions/{submission_id}/reject",
    responses=DECISION_ERRORS | {503: {"description": "Nextcloud could not delete the file"}},
)
async def reject(
    request: Request,
    submission_id: str,
    body: RejectRequest,
    curator: CurrentCurator,
    session: DbSession,
) -> SubmissionOut:
    """Reject a pending submission, with a reason."""
    resources: Resources = request.app.state.resources
    submission = await _locked(session, submission_id)
    previous = _change(submission, "rejected")
    await _delete_quarantined(
        resources.files, f"{resources.settings.quarantine_folder}/{submission.id}"
    )
    submission.quarantine_path = None
    _record(session, submission, previous, curator, body.reason)
    await session.commit()

    await _publish_decided(resources, submission, curator, body.reason)
    return SubmissionOut.model_validate(submission, from_attributes=True)


async def _locked(session: AsyncSession, submission_id: str) -> Submission:
    # The row stays locked until the decision is committed, so two curators
    # deciding at the same time cannot both succeed.
    submission = await session.get(Submission, submission_id, with_for_update=True)
    if submission is None:
        raise Problem(404, "There is no submission with this id.")
    return submission


def _change(submission: Submission, target: str) -> str:
    """Set the new status; 409 if the life cycle does not allow it."""
    previous = submission.status
    if not can_change(previous, target):
        raise Problem(
            409,
            f"The submission is {previous}, so it cannot be {target}.",
            slug="invalid-transition",
            title="Invalid status change",
            current_status=previous,
        )
    submission.status = target
    submission.updated_at = datetime.now(UTC)
    return previous


def _record(
    session: AsyncSession,
    submission: Submission,
    previous: str,
    curator: User,
    reason: str | None = None,
) -> None:
    session.add(
        StatusChange(
            submission_id=submission.id,
            from_status=previous,
            to_status=submission.status,
            actor=curator.id,
            reason=reason,
            changed_at=submission.updated_at,
        )
    )


async def _delete_quarantined(files: NextcloudFiles, folder: str) -> None:
    for delay in (*DELETE_RETRY_DELAYS, None):
        try:
            await files.delete(folder)
            return
        except FilesError as exc:
            if delay is None:
                logger.warning("Cannot delete %s: %s", folder, exc)
                raise Problem(503, "Nextcloud could not delete the file, try again later.") from exc
            await asyncio.sleep(delay)


async def _publish_decided(
    resources: Resources, submission: Submission, curator: User, reason: str | None
) -> None:
    data = {
        "submission_id": submission.id,
        "decision": submission.status,
        "reason": reason,
        "filename": submission.filename,
        "collection": submission.collection,
        "submitted_by": submission.submitted_by,
    }
    try:
        await resources.events.publish("submission.decided", curator.id, data)
    except RedisError as exc:
        # The decision is stored: answer the curator anyway.
        logger.error("Cannot publish submission.decided for %s: %s", submission.id, exc)
