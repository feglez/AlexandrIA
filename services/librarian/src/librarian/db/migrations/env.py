"""Alembic environment.

At start-up the service passes an open connection (see librarian.db.migrate).
From the command line (`uv run alembic ...`) it connects to DATABASE_URL.
"""

import asyncio
import os

from alembic import context
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from librarian.db.models import Base


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_from_command_line() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported.")

connection = context.config.attributes.get("connection")
if connection is not None:
    run_migrations(connection)
else:
    asyncio.run(run_from_command_line())
