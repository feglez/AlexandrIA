import httpx
from sqlalchemy import text

from librarian.app import create_public_app
from librarian.config import Settings
from librarian.db.migrate import upgrade_database
from librarian.resources import Resources
from tests.embeddings import FakeEmbedder


def test_migrations_can_run_again(migrated_database_url: str) -> None:
    # Another replica starting later finds nothing to do.
    upgrade_database(migrated_database_url)


async def test_service_reaches_postgres_and_redis(
    migrated_database_url: str, redis_url: str
) -> None:
    settings = Settings(
        database_url=migrated_database_url,
        redis_url=redis_url,
        nextcloud_service_password="service-password",
    )
    resources = Resources.from_settings(settings, FakeEmbedder())
    try:
        async with resources.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == "0003"
        assert await resources.redis.ping()

        transport = httpx.ASGITransport(app=create_public_app(resources))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
        assert response.json() == {"status": "ok"}
    finally:
        await resources.aclose()
