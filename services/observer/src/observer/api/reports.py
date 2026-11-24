"""Monthly cost-split report (README section 5.4).

Only for months that are over (UTC). Every member with usage up to the end
of the month or a storage snapshot taken before it is in the report. Their
storage is their personal storage in the last snapshot taken before the
month ended (0 without one) plus their contribution to the library at the
end of the month. The monthly cost (MONTHLY_COST_EUR) is split in proportion
to it (`observer.cost_split`).
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on

from observer import ledger
from observer.auth import CurrentCurator
from observer.cost_split import split_cost
from observer.db.models import StorageSnapshot
from observer.db.session import DbSession
from observer.problems import Problem
from observer.resources import Resources

router = APIRouter(prefix="/api/v1", tags=["reports"])

PeriodPath = Annotated[str, Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2026-10"])]
SPLIT_RULE = "proportional_to_storage"


class MemberShare(BaseModel):
    user: str
    storage_bytes: int
    contributed_bytes: int
    share: float
    amount_eur: float
    submissions: int
    searches: int
    questions: int


class Report(BaseModel):
    period: str
    monthly_cost_eur: float
    split_rule: str
    members: list[MemberShare]


def _end_of(period: str) -> datetime:
    """The first moment after the month."""
    year, month = (int(part) for part in period.split("-"))
    return datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)


@router.get("/reports/{period}")
async def monthly_report(
    period: PeriodPath, request: Request, curator: CurrentCurator, session: DbSession
) -> Report:
    """Who pays what of the monthly cost, and each member's usage that month."""
    resources: Resources = request.app.state.resources
    if period >= ledger.period_of(datetime.now(UTC)):
        raise Problem(
            409,
            f"The report of {period} is available once the month is over.",
            slug="period-not-closed",
            title="Month not over",
        )
    usage = {row.member: row for row in await session.execute(ledger.totals(period))}
    last_snapshots = (
        select(StorageSnapshot.member, StorageSnapshot.used_bytes)
        .where(StorageSnapshot.taken_at < _end_of(period))
        .order_by(StorageSnapshot.member, StorageSnapshot.taken_at.desc())
        .ext(distinct_on(StorageSnapshot.member))
    )
    personal = {row.member: row.used_bytes for row in await session.execute(last_snapshots)}

    def counted(member: str, name: str) -> int:
        row = usage.get(member)
        return int(getattr(row, name)) if row is not None else 0

    members = sorted(usage.keys() | personal.keys())
    contributed = {member: max(counted(member, "contributed_bytes"), 0) for member in members}
    storage = {member: personal.get(member, 0) + contributed[member] for member in members}
    cost = resources.settings.monthly_cost_eur
    shares = split_cost(cost, storage)
    return Report(
        period=period,
        monthly_cost_eur=float(cost),
        split_rule=SPLIT_RULE,
        members=[
            MemberShare(
                user=member,
                storage_bytes=storage[member],
                contributed_bytes=contributed[member],
                share=float(shares[member].share),
                amount_eur=float(shares[member].amount),
                submissions=counted(member, "submissions"),
                searches=counted(member, "searches"),
                questions=counted(member, "questions"),
            )
            for member in members
        ],
    )
