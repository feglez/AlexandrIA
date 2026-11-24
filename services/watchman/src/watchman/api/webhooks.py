"""Internal API: the webhooks of Nextcloud's webhook_listeners app.

It is only served on the internal port, which is never published. The
bootstrap job registers two listeners, for moved or renamed nodes and for
deleted nodes; Nextcloud sends WEBHOOK_SECRET with each call as a bearer
token. The changes to the library are made in watchman.changes, and the
events are emitted once they are committed.
"""

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError

from watchman import changes
from watchman.db.session import DbSession
from watchman.problems import Problem
from watchman.resources import Resources

logger = logging.getLogger(__name__)

RENAMED = "OCP\\Files\\Events\\Node\\NodeRenamedEvent"
DELETED = "OCP\\Files\\Events\\Node\\NodeDeletedEvent"
# Actor of the events when Nextcloud does not say who made the change.
ACTOR = "nextcloud"

_bearer = HTTPBearer(auto_error=False)


async def webhook_secret(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected = request.app.state.resources.settings.webhook_secret
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise Problem(
            401, "A valid webhook secret is required.", headers={"WWW-Authenticate": "Bearer"}
        )


# Nextcloud sends the JSON without a Content-Type header.
router = APIRouter(
    prefix="/internal", dependencies=[Depends(webhook_secret)], strict_content_type=False
)


class Node(BaseModel):
    path: str = Field(min_length=1)


class NodeEvent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_class: str = Field(alias="class")
    # Deleted nodes have `node`, moved ones `source` and `target`.
    node: Node | None = None
    source: Node | None = None
    target: Node | None = None


class Account(BaseModel):
    uid: str


class WebhookCall(BaseModel):
    event: NodeEvent
    user: Account | None = None


@router.post("/webhooks/nextcloud", status_code=204)
async def nextcloud_webhook(call: WebhookCall, request: Request, session: DbSession) -> None:
    """A node of the service account was moved, renamed or deleted."""
    resources: Resources = request.app.state.resources
    settings = resources.settings
    library = changes.Library(settings.nextcloud_service_user, settings.library_folder)
    event = call.event
    found: list[changes.Change] = []
    if event.event_class == DELETED and event.node is not None:
        path = library.path(event.node.path)
        if path is not None:
            found = await changes.deleted(session, path)
    elif event.event_class == RENAMED and event.source is not None and event.target is not None:
        source = library.path(event.source.path)
        if source is not None:
            found = await changes.moved(session, source, library.path(event.target.path))
    elif event.event_class in (DELETED, RENAMED):
        raise Problem(422, "The event has no node, or no source and target.")
    await session.commit()

    actor = call.user.uid if call.user else ACTOR
    for change in found:
        logger.info("%s %s", change.type, change.data["path"])
        try:
            await resources.events.publish(change.type, actor, change.data)
        except RedisError as exc:
            logger.error("Cannot publish %s for %s: %s", change.type, change.data["path"], exc)
