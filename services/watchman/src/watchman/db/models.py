"""Database tables (SQLAlchemy declarative models)."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import DateTime

# Stable constraint names, so Alembic can generate migrations that drop them.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Life cycle of a submission (README section 1.5).
SUBMISSION_STATUSES = (
    "checking",
    "pending_review",
    "approved",
    "rejected",
    "withdrawn",
    "published",
    "failed",
)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _status_in(column: str) -> str:
    return f"{column} IN ({', '.join(repr(status) for status in SUBMISSION_STATUSES)})"


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (CheckConstraint(_status_in("status"), name="status"),)

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    filename: Mapped[str] = mapped_column(Text)
    collection: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    submitted_by: Mapped[str] = mapped_column(Text, index=True)
    # Result of each automatic check: [{"name": ..., "result": ..., "detail": ...}]
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Path of the file in the service account's home, while it is in quarantine.
    quarantine_path: Mapped[str | None] = mapped_column(Text)
    # Publication by the mover. The target path and the Nextcloud file id are
    # stored before each move, so a retry can tell whether an earlier move
    # worked (the id stays the same when a file is moved).
    library_path: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[int | None] = mapped_column(BigInteger)
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    # Version of the moderation policy the submission was checked with.
    policy_version: Mapped[int] = mapped_column(ForeignKey("policies.version"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StatusChange(Base):
    """Every status change of a submission, with who made it and why."""

    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LibraryFile(Base):
    """A file published in the library. Used to detect duplicates by SHA-256."""

    __tablename__ = "library_files"

    # Nextcloud's file id, which stays the same when the file is moved.
    file_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    path: Mapped[str] = mapped_column(Text, unique=True)
    collection: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    submitted_by: Mapped[str] = mapped_column(Text)
    submission_id: Mapped[str | None] = mapped_column(
        ForeignKey("submissions.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PolicyVersion(Base):
    """One version of the moderation policy. The latest one is active."""

    __tablename__ = "policies"

    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    # The policy document, as validated by watchman.policy.Policy.
    document: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
