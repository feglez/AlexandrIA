"""An embedder for tests that need no model."""

import re
import zlib
from collections.abc import Sequence

from librarian.db.models import DIMENSIONS
from librarian.embeddings import Embedder


def vector(text: str) -> list[float]:
    """A bag of words: each word adds 1 to one dimension, chosen by its CRC-32.

    Texts that share words are close, texts that share none are orthogonal
    (unless two of their words fall in the same dimension). A text without
    words gets the first dimension, because a zero vector has no direction.
    """
    values = [0.0] * DIMENSIONS
    for word in re.findall(r"\w+", text.lower()):
        values[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0
    if not any(values):
        values[0] = 1.0
    return values


class FakeEmbedder(Embedder):
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def query(self, text: str) -> list[float]:
        self.queries.append(text)
        return vector(text)

    async def passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector(text) for text in texts]
