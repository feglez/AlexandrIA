"""Usage of the members (README section 5.4), from the usage ledger.

The library contribution is the sum of every change up to the end of the
month (files published minus files removed); the other figures count the
events of that month only. The quotas are the member's own ones, or else the
default ones.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import Row, func, select

from observer.auth import CurrentCurator, CurrentUser
from observer.db.models import UsageTotal
from observer.db.session import DbSession
from observer.ledger import period_of, totals
from observer.quotas import Quota, quotas_of
from observer.resources import Resources

router = APIRouter(prefix="/api/v1", tags=["usage"])

Period = Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2026-10"])]


class PersonalStorage(BaseModel):
    used_bytes: int
    # None when the member has no limit in Nextcloud.
    quota_bytes: int | None


class Contribution(BaseModel):
    used_bytes: int
    quota_bytes: int


class Submissions(BaseModel):
    this_period: int
    limit: int


class Count(BaseModel):
    this_period: int


class MyUsage(BaseModel):
    user: str
    period: str
    # None when Nextcloud could not tell.
    personal_storage: PersonalStorage | None
    library_contribution: Contribution
    submissions: Submissions
    searches: Count
    questions: Count


class MemberUsage(BaseModel):
    user: str
    library_contribution: Contribution
    submissions: Submissions
    searches: Count
    questions: Count


class UsagePage(BaseModel):
    period: str
    items: list[MemberUsage]
    total: int
    limit: int
    offset: int


def _member_usage(member: str, row: Row | None, quota: Quota) -> MemberUsage:
    def value(name: str) -> int:
        # PostgreSQL sums bigint columns as numeric.
        return int(getattr(row, name)) if row is not None else 0

    return MemberUsage(
        user=member,
        library_contribution=Contribution(
            used_bytes=value("contributed_bytes"), quota_bytes=quota.max_contribution_bytes
        ),
        submissions=Submissions(
            this_period=value("submissions"), limit=quota.max_submissions_per_month
        ),
        searches=Count(this_period=value("searches")),
        questions=Count(this_period=value("questions")),
    )


@router.get("/usage/me")
async def my_usage(request: Request, user: CurrentUser, session: DbSession) -> MyUsage:
    """My usage this month (UTC) and my personal storage in Nextcloud."""
    resources: Resources = request.app.state.resources
    period = period_of(datetime.now(UTC))
    row = (await session.execute(totals(period).where(UsageTotal.member == user.id))).first()
    quotas = await quotas_of(session, resources.settings, [user.id])
    usage = _member_usage(user.id, row, quotas[user.id])
    storage = await resources.storage.usage(user.id)
    return MyUsage(
        user=user.id,
        period=period,
        personal_storage=PersonalStorage.model_validate(storage, from_attributes=True)
        if storage
        else None,
        **usage.model_dump(exclude={"user"}),
    )


@router.get("/usage")
async def members_usage(
    request: Request,
    curator: CurrentCurator,
    session: DbSession,
    period: Period | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UsagePage:
    """Usage of every member with recorded usage, in a month (default: this
    month, UTC), ordered by user name."""
    resources: Resources = request.app.state.resources
    period = period or period_of(datetime.now(UTC))
    query = totals(period)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.execute(query.order_by(UsageTotal.member).limit(limit).offset(offset))
    ).all()
    quotas = await quotas_of(session, resources.settings, [row.member for row in rows])
    return UsagePage(
        period=period,
        items=[_member_usage(row.member, row, quotas[row.member]) for row in rows],
        total=total or 0,
        limit=limit,
        offset=offset,
    )
