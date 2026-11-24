import base64

import pytest

from watchman.config import Settings

NEXTCLOUD = "http://nextcloud.test"


def basic_auth(user: str, password: str) -> str:
    """Value of the Authorization header that HTTP Basic sends."""
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


class FakeRedis:
    """The few Redis commands the unit tests need, kept in memory."""

    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value.encode()

    async def aclose(self) -> None:
        pass


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://watchman:secret@localhost:5432/watchman",
        redis_url="redis://localhost:6379/1",
        nextcloud_url=NEXTCLOUD,
        public_base_url="https://alexandria.test",
    )
