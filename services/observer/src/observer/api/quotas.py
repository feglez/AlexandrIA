"""Contribution quotas of the members (README section 5.4).

Members can read their own quota, and curators everybody's. Only curators
can set one; it applies from the next quota check on.
"""

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from observer.auth import CurrentCurator, CurrentUser
from observer.db import models
from observer.db.session import DbSession
from observer.problems import Problem
from observer.quotas import Quota, quotas_of
from observer.resources import Resources

router = APIRouter(prefix="/api/v1", tags=["quotas"])

# Largest values the bigint and integer columns hold.
MAX_BYTES = 2**63 - 1
MAX_SUBMISSIONS = 2**31 - 1


class QuotaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_contribution_bytes: int = Field(ge=0, le=MAX_BYTES)
    max_submissions_per_month: int = Field(ge=0, le=MAX_SUBMISSIONS)


class QuotaOut(BaseModel):
    user: str
    max_contribution_bytes: int
    max_submissions_per_month: int
    # Who set it and when; null for the default quota.
    updated_by: str | None
    updated_at: datetime | None


def _out(member: str, quota: Quota) -> QuotaOut:
    return QuotaOut(
        user=member,
        max_contribution_bytes=quota.max_contribution_bytes,
        max_submissions_per_month=quota.max_submissions_per_month,
        updated_by=quota.updated_by,
        updated_at=quota.updated_at,
    )


@router.get("/quotas/{user}")
async def get_quota(
    user: str, request: Request, caller: CurrentUser, session: DbSession
) -> QuotaOut:
    """A member's quota: their own one, or the default one."""
    resources: Resources = request.app.state.resources
    if caller.id != user and resources.settings.curators_group not in caller.groups:
        raise Problem(403, "Only curators can see the quota of other members.")
    quotas = await quotas_of(session, resources.settings, [user])
    return _out(user, quotas[user])


@router.put("/quotas/{user}")
async def set_quota(
    user: str, quota: QuotaIn, curator: CurrentCurator, session: DbSession
) -> QuotaOut:
    """Give a member a quota of their own, or change it."""
    values = quota.model_dump()
    statement = insert(models.Quota).values(member=user, updated_by=curator.id, **values)
    result = await session.execute(
        statement.on_conflict_do_update(
            index_elements=[models.Quota.member],
            set_={**values, "updated_by": curator.id, "updated_at": func.now()},
        ).returning(models.Quota)
    )
    row = result.scalar_one()
    await session.commit()
    return QuotaOut(
        user=user,
        max_contribution_bytes=row.max_contribution_bytes,
        max_submissions_per_month=row.max_submissions_per_month,
        updated_by=row.updated_by,
        updated_at=row.updated_at,
    )
