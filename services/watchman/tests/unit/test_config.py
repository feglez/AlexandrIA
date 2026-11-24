import pytest

from watchman.config import Settings


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/watchman")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
    monkeypatch.setenv("PUBLIC_PORT", "9000")
    monkeypatch.setenv("NEXTCLOUD_SERVICE_PASSWORD", "service-password")
    monkeypatch.setenv("OBSERVER_INTERNAL_TOKEN", "observer-token")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db/watchman"
    assert settings.public_port == 9000
    assert settings.internal_port == 8001
    assert settings.curators_group == "curators"
    assert settings.nextcloud_service_user == "alexandria"
    assert settings.observer_internal_url == "http://observer:8001"


def test_database_and_redis_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValueError, match="database_url"):
        Settings()


def test_service_secrets_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/watchman")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
    monkeypatch.delenv("NEXTCLOUD_SERVICE_PASSWORD", raising=False)
    monkeypatch.delenv("OBSERVER_INTERNAL_TOKEN", raising=False)

    with pytest.raises(ValueError, match="nextcloud_service_password") as error:
        Settings()
    assert "observer_internal_token" in str(error.value)
