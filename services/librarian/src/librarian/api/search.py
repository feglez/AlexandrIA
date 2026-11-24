"""Semantic search (README section 5.3)."""

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, StringConstraints
from redis.exceptions import RedisError

from librarian.auth import CurrentUser
from librarian.resources import Resources
from librarian.search import nearest, snippet

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["search"])

# Spaces at either end do not count.
QueryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
CollectionName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]


class SearchResult(BaseModel):
    document_id: str
    path: str
    collection: str
    snippet: str
    # 1 - cosine distance between the query and the passage: higher is closer.
    score: float
    # The file in Nextcloud.
    url: str


class SearchResults(BaseModel):
    query: str
    took_ms: int
    results: list[SearchResult]


@router.get("/search")
async def search(
    request: Request,
    user: CurrentUser,
    q: Annotated[QueryText, Query(description="What to look for, 3 to 500 characters.")],
    k: Annotated[int, Query(ge=1, le=20, description="How many passages to return.")] = 5,
    collection: Annotated[
        CollectionName | None, Query(description="Only search this collection.")
    ] = None,
) -> SearchResults:
    """The passages of the library closest in meaning to `q`, the closest first."""
    resources: Resources = request.app.state.resources
    started = time.perf_counter()
    embedding = await resources.embedder.query(q)
    async with resources.sessions() as session:
        hits = await nearest(session, embedding, k, collection)
    took_ms = round((time.perf_counter() - started) * 1000)

    base_url = resources.settings.public_base_url.rstrip("/")
    results = [
        SearchResult(
            document_id=hit.document_id,
            path=hit.path,
            collection=hit.collection,
            snippet=snippet(hit.text),
            score=hit.score,
            url=f"{base_url}/f/{hit.file_id}",
        )
        for hit in hits
    ]
    await _publish_performed(resources, user.id, k, collection, len(results))
    return SearchResults(query=q, took_ms=took_ms, results=results)


async def _publish_performed(
    resources: Resources, member: str, k: int, collection: str | None, results: int
) -> None:
    # observer counts one search for the member. The query itself is not sent.
    data = {"k": k, "collection": collection, "results": results}
    try:
        await resources.events.publish("search.performed", member, data)
    except RedisError as exc:
        # The search worked: answer the member anyway.
        logger.error("Cannot publish search.performed for %s: %s", member, exc)
