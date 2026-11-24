import pytest

from observer.config import Settings


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/observer")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
    monkeypatch.setenv("PUBLIC_PORT", "9000")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db/observer"
    assert settings.public_port == 9000
    assert settings.internal_port == 8001
    assert settings.curators_group == "curators"


def test_database_and_redis_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValueError, match="database_url"):
        Settings()
