"""Client of observer's internal quota check (POST /internal/quota-checks).

The call has a short timeout. Any problem (observer down, timeout, an error
status or an unexpected body) gives no answer, and the caller treats the
check as unavailable: the submission then waits for a curator.
"""

import logging

import httpx
from pydantic import BaseModel, ValidationError

from watchman.checks import QuotaAnswer

logger = logging.getLogger(__name__)


class _Answer(BaseModel):
    allowed: bool
    remaining_bytes: int
    remaining_submissions: int


class QuotaClient:
    def __init__(self, client: httpx.AsyncClient, token: str) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}

    async def check(self, user: str, size_bytes: int) -> QuotaAnswer | None:
        try:
            response = await self._client.post(
                "/internal/quota-checks",
                json={"user": user, "size_bytes": size_bytes},
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("Quota check failed: %r", exc)
            return None
        if response.status_code != 200:
            logger.warning("Quota check failed: HTTP %s", response.status_code)
            return None
        try:
            answer = _Answer.model_validate_json(response.content)
        except ValidationError as exc:
            logger.warning("Quota check failed: unexpected answer: %s", exc)
            return None
        return QuotaAnswer(answer.allowed, answer.remaining_bytes, answer.remaining_submissions)
