"""Indexing: re-index jobs, and the queue of documents to index.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reindex_jobs",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("collection", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("documents", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed')",
            name=op.f("ck_reindex_jobs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reindex_jobs")),
    )
    op.alter_column("documents", "sha256", existing_type=sa.String(length=64), nullable=True)
    op.add_column(
        "documents",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column("documents", sa.Column("job_id", sa.String(length=30), nullable=True))
    op.create_foreign_key(
        op.f("fk_documents_job_id_reindex_jobs"),
        "documents",
        "reindex_jobs",
        ["job_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_documents_job_id"), "documents", ["job_id"])
    op.create_index(op.f("ix_documents_next_attempt_at"), "documents", ["next_attempt_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_documents_next_attempt_at"), table_name="documents")
    op.drop_index(op.f("ix_documents_job_id"), table_name="documents")
    op.drop_constraint(op.f("fk_documents_job_id_reindex_jobs"), "documents", type_="foreignkey")
    op.drop_column("documents", "job_id")
    op.drop_column("documents", "updated_at")
    # Files found by the reconciliation and never read have no SHA-256.
    op.execute("DELETE FROM documents WHERE sha256 IS NULL")
    op.alter_column("documents", "sha256", existing_type=sa.String(length=64), nullable=False)
    op.drop_table("reindex_jobs")
