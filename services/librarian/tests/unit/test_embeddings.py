"""FastEmbedEmbedder around a stand-in for fastembed's model."""

import threading
from collections.abc import Iterable, Iterator

import numpy as np
import pytest

from librarian import embeddings
from librarian.db.models import DIMENSIONS
from librarian.embeddings import FastEmbedEmbedder


class StubModel:
    """Answers like fastembed's TextEmbedding and records each call."""

    size = DIMENSIONS
    last: "StubModel"

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self.calls: list[tuple[str, list[str], str]] = []
        StubModel.last = self

    def _record(self, kind: str, texts: list[str]) -> None:
        self.calls.append((kind, texts, threading.current_thread().name))

    def query_embed(self, query: str) -> Iterator[np.ndarray]:
        self._record("query", [query])
        yield np.full(self.size, 0.5, dtype=np.float32)

    def passage_embed(self, texts: Iterable[str]) -> Iterator[np.ndarray]:
        texts = list(texts)
        self._record("passages", texts)
        for number, _ in enumerate(texts):
            yield np.full(self.size, number, dtype=np.float32)


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> type[StubModel]:
    monkeypatch.setattr(embeddings, "TextEmbedding", StubModel)
    monkeypatch.setattr(StubModel, "size", DIMENSIONS)
    return StubModel


def test_loads_the_model_once_and_computes_a_first_embedding(stub: type[StubModel]) -> None:
    embedder = FastEmbedEmbedder("some/model")
    try:
        assert stub.last.model_name == "some/model"
        assert [kind for kind, _, _ in stub.last.calls] == ["query"]
    finally:
        embedder.close()


async def test_embeds_in_a_thread_of_its_own(stub: type[StubModel]) -> None:
    embedder = FastEmbedEmbedder("some/model")
    try:
        vector = await embedder.query("electricity bill")
        passages = await embedder.passages(["first", "second", "third"])
    finally:
        embedder.close()

    assert vector == [0.5] * DIMENSIONS
    assert [values[0] for values in passages] == [0.0, 1.0, 2.0]
    assert all(len(values) == DIMENSIONS for values in passages)
    query_call, passages_call = stub.last.calls[1:]
    assert query_call[:2] == ("query", ["electricity bill"])
    assert passages_call[:2] == ("passages", ["first", "second", "third"])
    # Not in the thread that runs the event loop.
    assert query_call[2].startswith("embedder")
    assert passages_call[2].startswith("embedder")


async def test_no_passages_need_no_model(stub: type[StubModel]) -> None:
    embedder = FastEmbedEmbedder("some/model")
    try:
        assert await embedder.passages([]) == []
    finally:
        embedder.close()

    assert len(stub.last.calls) == 1


def test_a_model_of_another_size_is_refused(stub: type[StubModel]) -> None:
    stub.size = 768

    with pytest.raises(ValueError, match="gives 768 dimensions, the database stores 384"):
        FastEmbedEmbedder("some/model")
