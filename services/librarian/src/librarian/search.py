"""Semantic search: the passages of indexed documents closest to a query.

Closeness is the cosine distance between embeddings (pgvector's <=>
operator, which the HNSW index on chunks.embedding supports). Only documents
with status "indexed" are searched. Files only reach the index once they are
published (the file.published event), so files in quarantine or rejected
ones are never found.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Select, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from librarian.db.models import Chunk, Document

SNIPPET_LENGTH = 300


@dataclass(frozen=True)
class Hit:
    document_id: str
    path: str
    collection: str
    file_id: int
    text: str
    # Cosine distance: 0 for the same direction, up to 2 for the opposite one.
    distance: float

    @property
    def score(self) -> float:
        return round(1 - self.distance, 4)


def nearest_query(embedding: Sequence[float], k: int, collection: str | None = None) -> Select:
    """The query behind nearest()."""
    distance = Chunk.embedding.cosine_distance(embedding)
    statement = (
        select(
            Document.id,
            Document.path,
            Document.collection,
            Document.file_id,
            Chunk.text,
            distance.label("distance"),
        )
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.status == "indexed")
        .order_by(distance)
        .limit(k)
    )
    if collection is not None:
        statement = statement.where(Document.collection == collection)
    return statement


async def nearest(
    session: AsyncSession, embedding: Sequence[float], k: int, collection: str | None = None
) -> list[Hit]:
    """The k passages closest to the embedding, the closest first."""
    # The HNSW index finds the hnsw.ef_search (40) nearest passages and the
    # filters above come after it, so it could return fewer than k results
    # even when more exist. An iterative scan goes on until it has k.
    await session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
    rows = await session.execute(nearest_query(embedding, k, collection))
    return [Hit(*row) for row in rows]


def snippet(passage: str, length: int = SNIPPET_LENGTH) -> str:
    """The passage on one line, cut after a word to at most `length` characters."""
    flat = " ".join(passage.split())
    if len(flat) <= length:
        return flat
    cut = flat[: length - 3]
    if flat[length - 3] != " " and " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip(" ,.;:") + "..."
