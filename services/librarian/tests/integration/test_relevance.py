"""Relevance of the search with the real embedding model.

The model is the one baked into the image. fastembed loads it from
FASTEMBED_CACHE_PATH, or from a folder in the system's temporary directory,
and downloads it there the first time (about 240 MB). If it cannot be
downloaded these tests are skipped, except in CI, where they fail.

The checks compare ranks, not scores: the quantised model can give slightly
different scores on another CPU.
"""

import os
from collections.abc import Iterator

import httpx
import pytest
from fastembed import TextEmbedding

from librarian.config import Settings
from librarian.embeddings import Embedder, FastEmbedEmbedder
from librarian.resources import Resources
from tests.corpus import DOCUMENTS, QUERIES
from tests.integration.conftest import ALICE, index

pytestmark = pytest.mark.usefixtures("mocked")


@pytest.fixture(scope="module")
def model() -> Iterator[FastEmbedEmbedder]:
    name = Settings.model_fields["embedding_model"].default
    try:
        # Only downloads the model files that are missing.
        TextEmbedding(name, lazy_load=True)
    except Exception as exc:
        if os.environ.get("CI"):
            pytest.fail(f"The embedding model cannot be downloaded: {exc}")
        pytest.skip(f"The embedding model cannot be downloaded: {exc}")
    embedder = FastEmbedEmbedder(name)
    yield embedder
    embedder.close()


@pytest.fixture
def embedder(model: FastEmbedEmbedder) -> Embedder:
    return model


@pytest.fixture
async def library(resources: Resources) -> None:
    for path, passages in DOCUMENTS.items():
        await index(resources, path, *passages)


async def search(client: httpx.AsyncClient, **params: str) -> list[str]:
    response = await client.get("/api/v1/search", params=params, auth=ALICE)
    assert response.status_code == 200
    return [result["path"] for result in response.json()["results"]]


@pytest.mark.usefixtures("library")
@pytest.mark.parametrize(("query", "expected"), QUERIES.items())
async def test_finds_the_document_with_the_same_meaning(
    client: httpx.AsyncClient, query: str, expected: str
) -> None:
    collection = expected.split("/")[1]

    everywhere = await search(client, q=query, k="3")
    in_collection = await search(client, q=query, k="3", collection=collection)

    assert everywhere[0] == expected
    assert in_collection[0] == expected
    assert all(path.split("/")[1] == collection for path in in_collection)
