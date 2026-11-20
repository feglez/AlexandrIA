"""Integration tests use a real PostgreSQL and Redis.

Set TEST_DATABASE_URL (a superuser connection, any database) and
TEST_REDIS_URL. Each run creates a fresh database named test_observer.
Without them the tests are skipped, except in CI, where they fail.
"""

import asyncio
import os

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from observer.db.migrate import upgrade_database

TEST_DATABASE = "test_observer"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        if os.environ.get("CI"):
            pytest.fail(f"{name} must be set in CI")
        pytest.skip(f"{name} is not set")
    return value


async def _recreate_database(admin_url: str) -> None:
    engine = create_async_engine(admin_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE}" WITH (FORCE)'))
        await connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    admin_url = _required("TEST_DATABASE_URL")
    asyncio.run(_recreate_database(admin_url))
    url = make_url(admin_url).set(database=TEST_DATABASE)
    return url.render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def redis_url() -> str:
    return _required("TEST_REDIS_URL")


@pytest.fixture(scope="session")
def migrated_database_url(database_url: str) -> str:
    upgrade_database(database_url)
    return database_url
