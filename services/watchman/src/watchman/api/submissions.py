"""POST /api/v1/submissions: submit a file to the library.

The request is authenticated before its body is read. The body is a
multipart form with the fields `file`, `collection` and `note`. The file is
checked; if every check passes it is stored in the quarantine folder and the
submission waits for a curator (`pending_review`). If a check fails the
submission is recorded as `rejected` and the answer is a 422 problem with the
reason.
"""

import logging
import unicodedata
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, Request
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import FormData, UploadFile
from ulid import ULID

from watchman import checks
from watchman.api.collections import library_collections
from watchman.auth import CurrentUser, User
from watchman.db.models import LibraryFile, StatusChange, Submission
from watchman.db.session import DbSession
from watchman.files import FilesError, NextcloudFiles
from watchman.policy import ActivePolicy, Policy
from watchman.problems import Problem
from watchman.resources import Resources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["submissions"])

# Room for the multipart boundaries and the other form fields.
FORM_OVERHEAD_BYTES = 64 * 1024
MAX_NOTE_LENGTH = 1000
MAX_FILENAME_LENGTH = 250

# The form is read by hand (after the credentials are checked), so describe it
# for the OpenAPI document.
MULTIPART_FORM = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file", "collection"],
                    "properties": {
                        "file": {"type": "string", "format": "binary"},
                        "collection": {"type": "string"},
                        "note": {"type": "string", "maxLength": MAX_NOTE_LENGTH},
                    },
                }
            }
        },
    }
}


class SubmissionOut(BaseModel):
    id: str
    status: str
    filename: str
    collection: str
    size_bytes: int
    mime_type: str
    sha256: str
    submitted_by: str
    note: str | None
    checks: list[dict[str, str]]
    created_at: datetime


@router.post(
    "/submissions",
    status_code=202,
    openapi_extra=MULTIPART_FORM,
    responses={
        411: {"description": "No Content-Length header"},
        413: {"description": "The request is far over the size limit"},
        422: {"description": "Invalid form, or a check failed (the submission is rejected)"},
    },
)
async def submit(
    request: Request, user: CurrentUser, session: DbSession, policy: ActivePolicy
) -> SubmissionOut:
    _check_length(request, policy)
    resources: Resources = request.app.state.resources
    async with request.form(max_files=1, max_fields=4) as form:
        upload, collection, note = _read_form(form)
        filename = clean_filename(upload.filename)
        collections = await library_collections(resources)
        if collection not in collections:
            raise invalid(
                "collection", f"Unknown collection. Choose one of: {', '.join(collections)}."
            )
        facts = await run_in_threadpool(checks.inspect_file, upload.file, filename)
        if facts.size_bytes == 0:
            raise invalid("file", "The file is empty.")

        submission_id = f"sub_{ULID()}"
        folder = f"{resources.settings.quarantine_folder}/{submission_id}"
        submission = Submission(
            id=submission_id,
            filename=filename,
            collection=collection,
            note=note,
            size_bytes=facts.size_bytes,
            mime_type=facts.mime_type,
            sha256=facts.sha256,
            submitted_by=user.id,
        )
        existing_path = await session.scalar(
            select(LibraryFile.path).where(LibraryFile.sha256 == facts.sha256).limit(1)
        )
        # End the read transaction, so the connection is not held during the upload.
        await session.commit()
        results = await _run_checks(resources, user, facts, filename, policy, existing_path)
        rejected = checks.rejection(results)
        submission.checks = [check.to_json() for check in results]
        submission.status = "rejected" if rejected else "pending_review"
        submission.created_at = submission.updated_at = datetime.now(UTC)

        if rejected is None:
            submission.quarantine_path = f"{folder}/{filename}"
            await _store_in_quarantine(
                resources.files, folder, submission.quarantine_path, upload, facts.size_bytes
            )

    try:
        session.add(submission)
        await session.flush()
        session.add(
            StatusChange(
                submission_id=submission.id,
                from_status="checking",
                to_status=submission.status,
                actor=user.id,
                reason=rejected[0].reason if rejected else None,
            )
        )
        await session.commit()
    except Exception:
        if submission.quarantine_path:
            await _discard(resources.files, folder)
        raise

    await _publish_created(resources, submission)

    if rejected is not None:
        reason, failed = rejected
        extra = {"existing_path": existing_path} if reason.reason == "duplicate" else {}
        raise Problem(
            422,
            failed.detail,
            slug=reason.slug,
            title=reason.title,
            submission_id=submission.id,
            reason=reason.reason,
            checks=submission.checks,
            **extra,
        )
    return SubmissionOut.model_validate(submission, from_attributes=True)


def invalid(field: str, message: str, error_type: str = "value_error") -> Problem:
    """A 422 problem in the same format as FastAPI's validation errors."""
    return Problem(
        422,
        "The request is not valid.",
        slug="validation-error",
        title="Validation error",
        errors=[{"loc": ["body", field], "msg": message, "type": error_type}],
    )


def clean_filename(raw: str | None) -> str:
    """Keep only the last path component, so the file stays in its folder."""
    name = PurePosixPath((raw or "").replace("\\", "/")).name.strip()
    name = unicodedata.normalize("NFC", name)
    if name in ("", ".", ".."):
        raise invalid("file", "The file has no name.")
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise invalid("file", "The file name contains control characters.")
    if len(name) > MAX_FILENAME_LENGTH:
        raise invalid("file", f"The file name is longer than {MAX_FILENAME_LENGTH} characters.")
    return name


def _check_length(request: Request, policy: Policy) -> None:
    # Refuse a body that is certainly too large before reading it.
    length = request.headers.get("content-length", "")
    if not length.isdigit():
        raise Problem(411, "Send the request with a Content-Length header.")
    if int(length) > policy.max_size_bytes + FORM_OVERHEAD_BYTES:
        raise Problem(
            413,
            f"The file is over the limit of {checks.human_size(policy.max_size_bytes)}.",
            slug="file-too-large",
            title="File too large",
            reason="file_too_large",
            max_size_bytes=policy.max_size_bytes,
        )


def _read_form(form: FormData) -> tuple[UploadFile, str, str | None]:
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise invalid("file", "Field required", "missing")
    collection = form.get("collection")
    if not isinstance(collection, str) or not collection.strip():
        raise invalid("collection", "Field required", "missing")
    note = form.get("note")
    if note is not None and not isinstance(note, str):
        raise invalid("note", "Must be text.")
    note = (note or "").strip() or None
    if note is not None and len(note) > MAX_NOTE_LENGTH:
        raise invalid("note", f"The note is longer than {MAX_NOTE_LENGTH} characters.")
    return upload, collection.strip(), note


async def _run_checks(
    resources: Resources,
    user: User,
    facts: checks.FileFacts,
    filename: str,
    policy: Policy,
    existing_path: str | None,
) -> list[checks.Check]:
    results = [
        checks.size_limit(facts, policy),
        checks.mime_consistency(facts, filename),
        checks.blocked_type(facts, filename, policy),
        checks.duplicate(existing_path),
    ]
    # Only ask observer when the file is acceptable otherwise.
    if all(check.result == "pass" for check in results):
        results.append(checks.quota(await resources.quota.check(user.id, facts.size_bytes)))
    else:
        results.append(checks.QUOTA_SKIPPED)
    return results


async def _store_in_quarantine(
    files: NextcloudFiles, folder: str, path: str, upload: UploadFile, size_bytes: int
) -> None:
    try:
        await files.make_folder(folder)
        await files.upload(path, _chunks(upload), size_bytes)
    except FilesError as exc:
        logger.warning("Cannot store %s in the quarantine: %s", path, exc)
        await _discard(files, folder)
        raise Problem(503, "Nextcloud could not store the file, try again later.") from exc


async def _discard(files: NextcloudFiles, folder: str) -> None:
    try:
        await files.delete(folder)
    except FilesError as exc:
        logger.warning("Cannot delete %s: %s", folder, exc)


async def _chunks(upload: UploadFile) -> AsyncIterator[bytes]:
    await upload.seek(0)
    while chunk := await upload.read(checks.CHUNK_SIZE):
        yield chunk


async def _publish_created(resources: Resources, submission: Submission) -> None:
    data = {
        "submission_id": submission.id,
        "status": submission.status,
        "filename": submission.filename,
        "collection": submission.collection,
        "size_bytes": submission.size_bytes,
        "mime_type": submission.mime_type,
        "sha256": submission.sha256,
        "submitted_by": submission.submitted_by,
    }
    try:
        await resources.events.publish("submission.created", submission.submitted_by, data)
    except RedisError as exc:
        # The submission is stored: report it to the member anyway.
        logger.error("Cannot publish submission.created for %s: %s", submission.id, exc)
