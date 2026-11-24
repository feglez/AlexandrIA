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


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _status_in(column: str) -> str:
    return f"{column} IN ({', '.join(repr(status) for status in DOCUMENT_STATUSES)})"


class Document(Base):
    """A file of the library, as far as the search index is concerned."""

    __tablename__ = "documents"
    __table_args__ = (CheckConstraint(_status_in("status"), name="status"),)

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    # Nextcloud's file id, which stays the same when the file is moved.
    file_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    path: Mapped[str] = mapped_column(Text)
    collection: Mapped[str] = mapped_column(Text, index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), index=True)
    chunks: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last indexing error and the retries after it.
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
