"""GET /api/v1/search, with the fake embedder of tests.embeddings."""

import json
import math
import random
from typing import Any

import httpx
import pytest
import respx
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from librarian.db.models import DIMENSIONS, Chunk, Document
from librarian.resources import Resources
from librarian.search import nearest, nearest_query
from tests.embeddings import vector
from tests.integration.conftest import ALICE, BOB, PUBLIC_BASE_URL, events, index

pytestmark = pytest.mark.usefixtures("mocked")


async def search(
    client: httpx.AsyncClient, credentials: tuple[str, str] | None = ALICE, **params: Any
) -> httpx.Response:
    return await client.get("/api/v1/search", params=params, auth=credentials)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.hypot(*a) * math.hypot(*b))


async def test_returns_the_closest_passages_first(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    manual = await index(
        resources,
        "Library/Manuals/washing-machine.pdf",
        "The washing machine warranty lasts three years from the date of purchase.",
        "Clean the filter of the washing machine every month.",
    )
    await index(resources, "Library/Invoices/power.pdf", "Invoice for power supply, March.")

    response = await search(client, q="washing machine warranty")

    assert response.status_code == 200
    answer = response.json()
    assert answer["query"] == "washing machine warranty"
    assert isinstance(answer["took_ms"], int)
    assert answer["took_ms"] >= 0
    passage = "The washing machine warranty lasts three years from the date of purchase."
    assert answer["results"][0] == {
        "document_id": manual.id,
        "path": "Library/Manuals/washing-machine.pdf",
        "collection": "Manuals",
        "snippet": passage,
        "score": pytest.approx(cosine(vector("washing machine warranty"), vector(passage)), 1e-3),
        "url": f"{PUBLIC_BASE_URL}/f/{manual.file_id}",
    }
    assert answer["results"][1]["snippet"].startswith("Clean the filter")
    scores = [result["score"] for result in answer["results"]]
    assert scores == sorted(scores, reverse=True)
    assert len(scores) == 3


async def test_k_limits_the_results_and_is_5_by_default(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    for number in range(8):
        await index(resources, f"Library/Manuals/manual-{number}.pdf", f"Manual number {number}")

    assert len((await search(client, q="manual")).json()["results"]) == 5
    assert len((await search(client, q="manual", k=2)).json()["results"]) == 2
    assert len((await search(client, q="manual", k=20)).json()["results"]) == 8


async def test_only_indexed_documents_are_searched(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    indexed = await index(resources, "Library/Manuals/indexed.pdf", "Boiler manual")
    for status in ("pending", "failed", "skipped"):
        await index(resources, f"Library/Manuals/{status}.pdf", "Boiler manual", status=status)

    results = (await search(client, q="boiler manual", k=20)).json()["results"]

    assert [result["document_id"] for result in results] == [indexed.id]


async def test_the_collection_filter_keeps_only_that_collection(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    await index(resources, "Library/Manuals/boiler.pdf", "Gas boiler, March 2026")
    invoice = await index(resources, "Library/Invoices/boiler.pdf", "Gas boiler repair, March 2026")

    everything = (await search(client, q="gas boiler March 2026")).json()["results"]
    invoices = (await search(client, q="gas boiler March 2026", collection="Invoices")).json()
    medical = (await search(client, q="gas boiler March 2026", collection="Medical")).json()

    assert len(everything) == 2
    assert [result["document_id"] for result in invoices["results"]] == [invoice.id]
    assert medical["results"] == []
    # Spaces around the collection do not count either.
    spaced = (await search(client, q="gas boiler", collection=" Invoices ")).json()["results"]
    assert [result["document_id"] for result in spaced] == [invoice.id]


async def test_finds_nothing_in_an_empty_library(client: httpx.AsyncClient) -> None:
    response = await search(client, q="electricity bill")

    assert response.status_code == 200
    assert response.json()["results"] == []


async def test_long_passages_get_a_short_snippet(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    passage = "Warranty conditions. " + "The manufacturer repairs any fault for free. " * 20
    await index(resources, "Library/Manuals/long.pdf", passage)

    snippet = (await search(client, q="warranty conditions")).json()["results"][0]["snippet"]

    assert len(snippet) <= 300
    assert snippet.startswith("Warranty conditions. The manufacturer repairs")
    assert snippet.endswith("...")


async def test_spaces_around_the_query_do_not_count(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    response = await search(client, q="   washing machine  ")

    assert response.status_code == 200
    assert response.json()["query"] == "washing machine"
    assert resources.embedder.queries == ["washing machine"]
    # 500 characters plus spaces is still a valid query.
    assert (await search(client, q=f"  {'a' * 500}  ")).status_code == 200


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({}, "q"),
        ({"q": ""}, "q"),
        ({"q": "ab"}, "q"),
        ({"q": "   ab   "}, "q"),
        ({"q": "a" * 501}, "q"),
        ({"q": "warranty", "k": 0}, "k"),
        ({"q": "warranty", "k": 21}, "k"),
        ({"q": "warranty", "k": "many"}, "k"),
        ({"q": "warranty", "collection": " "}, "collection"),
    ],
)
async def test_invalid_searches_get_422(
    client: httpx.AsyncClient, resources: Resources, params: dict[str, Any], field: str
) -> None:
    response = await search(client, **params)

    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == f"{PUBLIC_BASE_URL}/problems/validation-error"
    assert [error["loc"] for error in problem["errors"]] == [["query", field]]
    assert resources.embedder.queries == []
    assert await events(resources) == []


async def test_credentials_are_required(
    client: httpx.AsyncClient, resources: Resources, mocked: respx.MockRouter
) -> None:
    anonymous = await search(client, credentials=None, q="warranty")
    wrong = await search(client, credentials=("alice", "wrong"), q="warranty")

    assert anonymous.status_code == 401
    assert anonymous.headers["WWW-Authenticate"] == 'Basic realm="AlexandrIA"'
    assert wrong.status_code == 401
    assert resources.embedder.queries == []
    assert await events(resources) == []


async def test_each_search_is_recorded_for_the_member(
    client: httpx.AsyncClient, resources: Resources
) -> None:
    await index(resources, "Library/Manuals/boiler.pdf", "Gas boiler manual")

    await search(client, q="secret boiler question", k=3, collection="Manuals")
    await search(client, credentials=BOB, q="anything at all")

    first, second = await events(resources)
    assert first["type"] == "search.performed"
    assert first["actor"] == "alice"
    assert first["data"] == {"k": 3, "collection": "Manuals", "results": 1}
    assert first["id"]
    assert first["occurred_at"].endswith("Z")
    # observer only needs to count it: the query stays in librarian.
    assert "secret" not in json.dumps(first)
    assert second["actor"] == "bob"
    # Nearest passages, however far: the only one in the library.
    assert second["data"] == {"k": 5, "collection": None, "results": 1}


async def test_the_search_answers_when_the_event_cannot_be_published(
    client: httpx.AsyncClient,
    resources: Resources,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await index(resources, "Library/Manuals/boiler.pdf", "Gas boiler manual")

    async def redis_down(*args: Any) -> str:
        raise RedisConnectionError("Connection refused")

    monkeypatch.setattr(resources.events, "publish", redis_down)

    response = await search(client, q="gas boiler")

    assert response.status_code == 200
    assert len(response.json()["results"]) == 1
    assert "Cannot publish search.performed for alice" in caplog.text


async def test_the_index_scan_finds_k_passages_after_the_filters(resources: Resources) -> None:
    # Dense random vectors, like a real model's: 60 passages of Manuals around
    # the query, and 3 of Invoices further away. The HNSW index looks at the
    # 40 nearest ones by default, all from Manuals, before the filters.
    rng = random.Random(7)  # noqa: S311 - test data, not a secret
    query = [rng.gauss(0, 1) for _ in range(DIMENSIONS)]

    def around(spread: float) -> list[float]:
        return [value + rng.gauss(0, spread) for value in query]

    async with resources.sessions.begin() as session:
        for number in range(63):
            collection = "Manuals" if number < 60 else "Invoices"
            document = Document(
                id=f"doc_{number:02}",
                file_id=number,
                path=f"Library/{collection}/{number}.pdf",
                collection=collection,
                sha256="0" * 64,
                status="indexed",
                chunks=1,
            )
            session.add(document)
            await session.flush()
            spread = 0.3 if collection == "Manuals" else 1.0
            session.add(Chunk(document_id=document.id, ord=0, text="-", embedding=around(spread)))

    statement = nearest_query(query, 3, "Invoices").compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    async with resources.sessions() as session:
        await session.execute(text("ANALYZE documents"))
        await session.execute(text("ANALYZE chunks"))
        # On such a small table PostgreSQL would filter and sort instead of
        # using the index: without sorting, the index is the only way.
        await session.execute(text("SET LOCAL enable_sort = off"))
        plan = await session.scalars(text(f"EXPLAIN {statement}"))
        hits = await nearest(session, query, 3, "Invoices")

    assert "Index Scan using ix_chunks_embedding" in "\n".join(plan)
    assert sorted(hit.path for hit in hits) == [
        f"Library/Invoices/{number}.pdf" for number in (60, 61, 62)
    ]
    assert [hit.distance for hit in hits] == sorted(hit.distance for hit in hits)
