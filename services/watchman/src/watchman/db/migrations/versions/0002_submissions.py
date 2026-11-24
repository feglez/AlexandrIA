"""Submissions, their status history and the files published in the library.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "submissions",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("collection", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("submitted_by", sa.Text(), nullable=False),
        sa.Column("checks", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("quarantine_path", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('checking', 'pending_review', 'approved', 'rejected', "
            "'withdrawn', 'published', 'failed')",
            name=op.f("ck_submissions_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submissions")),
    )
    op.create_index(op.f("ix_submissions_status"), "submissions", ["status"])
    op.create_index(op.f("ix_submissions_submitted_by"), "submissions", ["submitted_by"])

    op.create_table(
        "status_history",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("submission_id", sa.String(length=30), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_status_history_submission_id_submissions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_status_history")),
    )
    op.create_index(op.f("ix_status_history_submission_id"), "status_history", ["submission_id"])

    op.create_table(
        "library_files",
        sa.Column("file_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("collection", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("submitted_by", sa.Text(), nullable=False),
        sa.Column("submission_id", sa.String(length=30), nullable=True),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.id"],
            name=op.f("fk_library_files_submission_id_submissions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("file_id", name=op.f("pk_library_files")),
        sa.UniqueConstraint("path", name=op.f("uq_library_files_path")),
    )
    op.create_index(op.f("ix_library_files_sha256"), "library_files", ["sha256"])


def downgrade() -> None:
    op.drop_table("library_files")
    op.drop_table("status_history")
    op.drop_table("submissions")
