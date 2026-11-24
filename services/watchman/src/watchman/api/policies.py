"""The moderation policy, for curators.

GET /api/v1/policies    the active policy, with its version and author
PUT /api/v1/policies    store a new version; it applies to new submissions only

Older versions stay in the database with the curator who saved them.
"""

from fastapi import APIRouter

from watchman import policy
from watchman.auth import CurrentCurator
from watchman.db.session import DbSession
from watchman.policy import Policy, VersionedPolicy

router = APIRouter(prefix="/api/v1", tags=["policies"])

NOT_A_CURATOR = {403: {"description": "The user is not a curator"}}


@router.get("/policies", responses=NOT_A_CURATOR)
async def get_policy(curator: CurrentCurator, session: DbSession) -> VersionedPolicy:
    return await policy.latest(session)


@router.put(
    "/policies",
    responses=NOT_A_CURATOR | {422: {"description": "The policy document is not valid"}},
)
async def put_policy(body: Policy, curator: CurrentCurator, session: DbSession) -> VersionedPolicy:
    """Replace the policy. Every field is required."""
    return await policy.save(session, body, curator.id)
