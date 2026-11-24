"""Contribution quotas of the members (README section 5.4).

A curator can give a member a quota of their own. Every other member has the
default one from the settings (DEFAULT_MAX_CONTRIBUTION_BYTES and
DEFAULT_MAX_SUBMISSIONS_PER_MONTH).

The quota check tells watchman whether a member can contribute a file of
`size_bytes` more: their contribution to the library (published minus
removed, every month) plus the file must fit in `max_contribution_bytes`, and
their accepted submissions this month plus this one in
`max_submissions_per_month`. The remaining figures are what the member has
left once this submission is accepted or, when it is not allowed, what they
have left now. They are never below 0.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from observer.config import Settings
from observer.db import models


@dataclass(frozen=True)
class Quota:
    max_contribution_bytes: int
    max_submissions_per_month: int
    # Who set it and when; None for the default quota.
    updated_by: str | None = None
    updated_at: datetime | None = None


def default_quota(settings: Settings) -> Quota:
    return Quota(
        max_contribution_bytes=settings.default_max_contribution_bytes,
        max_submissions_per_month=settings.default_max_submissions_per_month,
    )


async def quotas_of(
    session: AsyncSession, settings: Settings, members: Iterable[str]
) -> dict[str, Quota]:
    """The quota of each member: their own one, or else the default one."""
    members = list(members)
    rows = await session.scalars(select(models.Quota).where(models.Quota.member.in_(members)))
    own = {
        row.member: Quota(
            max_contribution_bytes=row.max_contribution_bytes,
            max_submissions_per_month=row.max_submissions_per_month,
            updated_by=row.updated_by,
            updated_at=row.updated_at,
        )
        for row in rows
    }
    default = default_quota(settings)
    return {member: own.get(member, default) for member in members}


@dataclass(frozen=True)
class QuotaCheck:
    allowed: bool
    remaining_bytes: int
    remaining_submissions: int


def check(quota: Quota, contributed_bytes: int, submissions: int, size_bytes: int) -> QuotaCheck:
    """Can a member who contributed `contributed_bytes` and made `submissions`
    this month submit a file of `size_bytes`?"""
    bytes_left = quota.max_contribution_bytes - contributed_bytes
    submissions_left = quota.max_submissions_per_month - submissions
    allowed = size_bytes <= bytes_left and submissions_left >= 1
    if allowed:
        bytes_left -= size_bytes
        submissions_left -= 1
    return QuotaCheck(allowed, max(bytes_left, 0), max(submissions_left, 0))
