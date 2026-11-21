"""Schema migrations, applied when the service starts.

The migrations run inside one transaction that holds a PostgreSQL advisory
lock, so several replicas can start at the same time: the first one migrates
and the others wait and then find nothing left to do.
"""

import asyncio
import zlib
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

MIGRATIONS = Path(__file__).parent / "migrations"
LOCK_ID = zlib.crc32(b"librarian.migrations")


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def upgrade_database(database_url: str) -> None:
    """Bring the database to the latest revision. Call it before starting the event loop."""
    asyncio.run(_upgrade(database_url))


async def _upgrade(database_url: str) -> None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": LOCK_ID})
            await connection.run_sync(_run_alembic)
    finally:
        await engine.dispose()


def _run_alembic(connection: Connection) -> None:
    config = alembic_config()
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
