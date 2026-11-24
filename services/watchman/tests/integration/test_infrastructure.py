import httpx
from sqlalchemy import text

from watchman.app import create_public_app
from watchman.config import Settings
from watchman.db.migrate import upgrade_database
from watchman.resources import Resources


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
        observer_internal_token="observer-token",
    )
    resources = Resources.from_settings(settings)
    try:
        async with resources.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == "0002"
        assert await resources.redis.ping()

        transport = httpx.ASGITransport(app=create_public_app(resources))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
        assert response.json() == {"status": "ok"}
    finally:
        await resources.aclose()
