"""Documents of the library and their passages with embeddings.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Created by the PostgreSQL init script in compose (only a superuser can),
    # so this does nothing there. Test databases are created by a superuser.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=30), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("collection", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("chunks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'indexed', 'failed', 'skipped')",
            name=op.f("ck_documents_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("file_id", name=op.f("uq_documents_file_id")),
    )
    op.create_index(op.f("ix_documents_collection"), "documents", ["collection"])
    op.create_index(op.f("ix_documents_status"), "documents", ["status"])
    op.create_table(
        "chunks",
        sa.Column("document_id", sa.String(length=30), nullable=False),
        sa.Column("ord", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_chunks_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id", "ord", name=op.f("pk_chunks")),
    )
    op.create_index(
        "ix_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    # The extension stays: it belongs to the superuser that created it.
    op.drop_table("chunks")
    op.drop_table("documents")
