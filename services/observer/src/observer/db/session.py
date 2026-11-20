from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession


async def _session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.resources.sessions() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(_session)]
