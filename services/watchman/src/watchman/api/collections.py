"""GET /api/v1/collections: the library folders that accept submissions."""

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from watchman.auth import CurrentUser
from watchman.files import FilesError
from watchman.problems import Problem
from watchman.resources import Resources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["collections"])


class Collection(BaseModel):
    name: str
    path: str


class CollectionList(BaseModel):
    items: list[Collection]
    total: int


async def library_collections(resources: Resources) -> list[str]:
    """Top-level folders of the library, read from Nextcloud."""
    try:
        return await resources.files.list_folders(resources.settings.library_folder)
    except FilesError as exc:
        logger.warning("Cannot list the collections: %s", exc)
        raise Problem(503, "Nextcloud is not reachable, try again later.") from exc


@router.get("/collections")
async def list_collections(request: Request, user: CurrentUser) -> CollectionList:
    resources: Resources = request.app.state.resources
    library = resources.settings.library_folder
    names = await library_collections(resources)
    return CollectionList(
        items=[Collection(name=name, path=f"{library}/{name}") for name in names],
        total=len(names),
    )
