"""Text embeddings for the search index.

The model is loaded once, when the service starts (see librarian.__main__).
Computing an embedding is CPU-bound, so it runs in a thread of its own and
the event loop stays free for other requests. That thread computes one
embedding at a time: ONNX Runtime already uses every core for each one.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor

from fastembed import TextEmbedding

from librarian.db.models import DIMENSIONS


class Embedder(ABC):
    """Turns texts into vectors of DIMENSIONS floats."""

    @abstractmethod
    async def query(self, text: str) -> list[float]:
        """The embedding of a search query or a question."""

    @abstractmethod
    async def passages(self, texts: Sequence[str]) -> list[list[float]]:
        """The embeddings of passages of documents, in the same order."""

    def close(self) -> None:  # noqa: B027 - most embedders have nothing to release
        """Release what the embedder holds."""


class FastEmbedEmbedder(Embedder):
    """An ONNX model run by fastembed, from FASTEMBED_CACHE_PATH.

    The image has the model in that folder. Elsewhere fastembed downloads it
    the first time.
    """

    def __init__(self, model_name: str) -> None:
        self._model = TextEmbedding(model_name)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedder")
        # The first embedding is slower: compute it now, not in the first search.
        size = len(self._query("warm-up"))
        if size != DIMENSIONS:
            self.close()
            raise ValueError(
                f"{model_name} gives {size} dimensions, the database stores {DIMENSIONS}"
            )

    def _query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()

    def _passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.passage_embed(list(texts))]

    async def query(self, text: str) -> list[float]:
        return await asyncio.get_running_loop().run_in_executor(self._executor, self._query, text)

    async def passages(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, self._passages, texts
        )

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
