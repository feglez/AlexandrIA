"""The documents of the search index (README section 5.3)."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select

from librarian.auth import CurrentCurator, CurrentUser
from librarian.db.models import Document
from librarian.db.session import DbSession
from librarian.problems import Problem

router = APIRouter(prefix="/api/v1", tags=["documents"])

Status = Literal["pending", "indexed", "failed", "skipped"]
CollectionName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
NOT_FOUND = "There is no such document in the index."


class DocumentOut(BaseModel):
    id: str
    path: str
    collection: str
    status: str
    # Passages in the index.
    chunks: int
    indexed_at: datetime | None
    # Why the last attempt failed or the file was skipped.
    error: str | None
    # Failed attempts since the last success, and when the next one is due.
    attempts: int
    next_attempt_at: datetime | None
    url: str


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


def document_out(request: Request, document: Document) -> DocumentOut:
    base_url = request.app.state.resources.settings.public_base_url.rstrip("/")
    return DocumentOut(
        id=document.id,
        path=document.path,
        collection=document.collection,
        status=document.status,
        chunks=document.chunks,
        indexed_at=document.indexed_at,
        error=document.error,
        attempts=document.attempts,
        next_attempt_at=document.next_attempt_at,
        url=f"{base_url}/f/{document.file_id}",
    )


@router.get("/documents")
async def list_documents(
    request: Request,
    curator: CurrentCurator,
    session: DbSession,
    status: Status | None = None,
    collection: CollectionName | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentPage:
    """The documents of the index, by path. Curators only."""
    query = select(Document)
    if status is not None:
        query = query.where(Document.status == status)
    if collection is not None:
        query = query.where(Document.collection == collection)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = await session.scalars(
        query.order_by(Document.path, Document.id).limit(limit).offset(offset)
    )
    return DocumentPage(
        items=[document_out(request, row) for row in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/documents/{document_id}", responses={404: {"description": "No such document in the index"}}
)
async def get_document(
    request: Request, document_id: str, user: CurrentUser, session: DbSession
) -> DocumentOut:
    """One document of the index. Every member can read the library, so every
    member can see its documents."""
    document = await session.get(Document, document_id)
    if document is None:
        raise Problem(404, NOT_FOUND)
    return document_out(request, document)
