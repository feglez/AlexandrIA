"""Internal API: the quota check that watchman makes (README section 5.4).

It is only served on the internal port, which is never published. Every call
needs the service token (OBSERVER_INTERNAL_TOKEN) as a bearer token.
"""

import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from observer import ledger, quotas
from observer.db.models import UsageTotal
from observer.db.session import DbSession
from observer.problems import Problem
from observer.resources import Resources

_bearer = HTTPBearer(auto_error=False)


async def service_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    expected = request.app.state.resources.settings.observer_internal_token
    if credentials is None or not hmac.compare_digest(
        credentials.credentials.encode(), expected.encode()
    ):
        raise Problem(
            401, "A valid service token is required.", headers={"WWW-Authenticate": "Bearer"}
        )


router = APIRouter(prefix="/internal", dependencies=[Depends(service_token)])


class QuotaCheckIn(BaseModel):
    user: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class QuotaCheckOut(BaseModel):
    allowed: bool
    remaining_bytes: int
    remaining_submissions: int


@router.post("/quota-checks")
async def quota_check(body: QuotaCheckIn, request: Request, session: DbSession) -> QuotaCheckOut:
    """Can this member contribute `size_bytes` more?"""
    resources: Resources = request.app.state.resources
    period = ledger.period_of(datetime.now(UTC))
    row = (
        await session.execute(ledger.totals(period).where(UsageTotal.member == body.user))
    ).first()
    quota = (await quotas.quotas_of(session, resources.settings, [body.user]))[body.user]
    result = quotas.check(
        quota,
        contributed_bytes=int(row.contributed_bytes) if row else 0,
        submissions=int(row.submissions) if row else 0,
        size_bytes=body.size_bytes,
    )
    return QuotaCheckOut(
        allowed=result.allowed,
        remaining_bytes=result.remaining_bytes,
        remaining_submissions=result.remaining_submissions,
    )
