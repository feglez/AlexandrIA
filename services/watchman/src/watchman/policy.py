"""Moderation policy: the limits the automatic checks apply to new submissions,
and the rule that approves some of them without a curator.

Every version of the policy is kept in the `policies` table with the curator
who saved it. The latest version is the active one, and each submission
records the version it was checked with, so a change only applies to new
submissions. The migration that creates the table stores DEFAULT_POLICY as
version 1.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Self

from fastapi import Depends, Request
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from watchman.db.models import PolicyVersion

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


_NAME = r"[a-z0-9][a-z0-9!#$&^_.+-]*"
_MIME_TYPE = re.compile(rf"{_NAME}/(\*|{_NAME})")
_EXTENSION = re.compile(r"\.[a-z0-9]+")


def _mime_type(value: str) -> str:
    """A type and subtype, or a type and "*" for every subtype. Stored in lowercase."""
    value = value.strip().lower()
    if not _MIME_TYPE.fullmatch(value):
        raise ValueError('must be "type/subtype" or "type/*", for example "application/pdf"')
    return value


def _extension(value: str) -> str:
    """One dot and the extension. A file name's extension is only its last
    part, so ".tar.gz" could never match anything."""
    value = value.strip().lower()
    if not _EXTENSION.fullmatch(value):
        raise ValueError('must be a dot and letters or digits, for example ".exe"')
    return value


MimeType = Annotated[str, AfterValidator(_mime_type)]
Extension = Annotated[str, AfterValidator(_extension)]
GroupName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Size = Annotated[int, Field(gt=0, strict=True)]


class AutoApprove(BaseModel):
    """Submissions from these groups, up to this size, skip the curator when
    every check passes."""

    model_config = ConfigDict(extra="forbid")

    groups: list[GroupName]
    max_size_bytes: Size


class Policy(BaseModel):
    """The document of PUT /api/v1/policies. Every field is required."""

    model_config = ConfigDict(extra="forbid")

    max_size_bytes: Size
    allowed_mime_types: list[MimeType]
    blocked_extensions: list[Extension]
    auto_approve: AutoApprove

    @model_validator(mode="after")
    def _auto_approve_within_limit(self) -> Self:
        if self.auto_approve.max_size_bytes > self.max_size_bytes:
            raise ValueError("auto_approve.max_size_bytes cannot be larger than max_size_bytes")
        return self


# The example policy of the API specification (README 5.2).
DEFAULT_POLICY = Policy(
    max_size_bytes=50 * 1024 * 1024,
    allowed_mime_types=["application/pdf", "text/*", "image/jpeg", "image/png", DOCX],
    blocked_extensions=[".exe", ".msi", ".bat", ".sh", ".apk"],
    auto_approve=AutoApprove(groups=["trusted"], max_size_bytes=10 * 1024 * 1024),
)


class VersionedPolicy(BaseModel):
    version: int
    created_by: str
    created_at: datetime
    policy: Policy


def _versioned(row: PolicyVersion) -> VersionedPolicy:
    return VersionedPolicy(
        version=row.version,
        created_by=row.created_by,
        created_at=row.created_at,
        policy=Policy.model_validate(row.document),
    )


async def latest(session: AsyncSession) -> VersionedPolicy:
    row = await session.scalar(
        select(PolicyVersion).order_by(PolicyVersion.version.desc()).limit(1)
    )
    if row is None:
        raise RuntimeError("The policies table is empty: the migrations did not run.")
    return _versioned(row)


async def save(session: AsyncSession, policy: Policy, author: str) -> VersionedPolicy:
    """Store the policy as the next version and commit."""
    # Writers wait for each other, so the versions have no gaps or clashes.
    # Readers are not blocked.
    await session.execute(text("LOCK TABLE policies IN EXCLUSIVE MODE"))
    current = await session.scalar(select(func.max(PolicyVersion.version)))
    row = PolicyVersion(
        version=(current or 0) + 1,
        document=policy.model_dump(mode="json"),
        created_by=author,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.commit()
    return _versioned(row)


async def active_policy(request: Request) -> VersionedPolicy:
    # A session of its own: no connection is held while the upload is read.
    async with request.app.state.resources.sessions() as session:
        return await latest(session)


ActivePolicy = Annotated[VersionedPolicy, Depends(active_policy)]
