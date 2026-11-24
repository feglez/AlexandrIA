"""Database tables (SQLAlchemy declarative models)."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    func,
    text,
)
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

# Length of the embedding vectors of paraphrase-multilingual-MiniLM-L12-v2.
DIMENSIONS = 384

# Indexing state of a document (README section 5.3). Only indexed documents
# are searched.
DOCUMENT_STATUSES = ("pending", "indexed", "failed", "skipped")
# State of a re-index job.
JOB_STATUSES = ("queued", "running", "done", "failed")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


class ReindexJob(Base):
    """A re-index of the whole library or of one collection, asked by a curator."""

    __tablename__ = "reindex_jobs"
    __table_args__ = (CheckConstraint(_one_of("status", JOB_STATUSES), name="status"),)

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    collection: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    requested_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Documents the job queued for indexing; None until it has queued them.
    documents: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class Document(Base):
    """A file of the library, as far as the search index is concerned.

    The document waits to be indexed (again) while `next_attempt_at` is set.
    It keeps its status until then, so an indexed document stays searchable.
    """

    __tablename__ = "documents"
    __table_args__ = (CheckConstraint(_one_of("status", DOCUMENT_STATUSES), name="status"),)

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    # Nextcloud's file id, which stays the same when the file is moved.
    file_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    path: Mapped[str] = mapped_column(Text)
    collection: Mapped[str] = mapped_column(Text, index=True)
    # Of the content last indexed or published; None for a file that the
    # reconciliation found and that has not been read yet.
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), index=True)
    chunks: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why the last attempt failed or the file was skipped, and the failed
    # attempts since the last success.
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    # Last change of the path or of the content (an event, the reconciliation).
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The re-index job that queued the document, until the document is done.
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("reindex_jobs.id", ondelete="SET NULL"), index=True
    )


class Chunk(Base):
    """A passage of a document with its embedding."""

    __tablename__ = "chunks"
    __table_args__ = (
        # Approximate nearest neighbours by cosine distance (the <=> operator).
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    # Position of the passage in the document, from 0.
    ord: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(DIMENSIONS))
