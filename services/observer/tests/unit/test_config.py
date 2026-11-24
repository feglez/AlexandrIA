from decimal import Decimal

import pytest

from observer.config import Settings


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/observer")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
    monkeypatch.setenv("PUBLIC_PORT", "9000")
    monkeypatch.setenv("NEXTCLOUD_OBSERVER_PASSWORD", "observer-password")
    monkeypatch.setenv("OBSERVER_INTERNAL_TOKEN", "observer-token")
    monkeypatch.setenv("MONTHLY_COST_EUR", "7.25")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u:p@db/observer"
    assert settings.public_port == 9000
    assert settings.internal_port == 8001
    assert settings.curators_group == "curators"
    assert settings.nextcloud_observer_user == "observer"
    assert settings.events_stream == "alexandria.events"
    assert settings.events_group == "observer"
    assert settings.observer_internal_token == "observer-token"
    assert settings.members_group == "members"
    assert settings.storage_snapshot_seconds == 24 * 3600
    assert settings.monthly_cost_eur == Decimal("7.25")


def test_database_and_redis_are_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValueError, match="database_url"):
        Settings()


def test_observer_account_password_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/observer")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
    monkeypatch.delenv("NEXTCLOUD_OBSERVER_PASSWORD", raising=False)

    with pytest.raises(ValueError, match="nextcloud_observer_password"):
        Settings()


def test_internal_token_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/observer")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
    monkeypatch.setenv("NEXTCLOUD_OBSERVER_PASSWORD", "observer-password")
    monkeypatch.delenv("OBSERVER_INTERNAL_TOKEN", raising=False)

    with pytest.raises(ValueError, match="observer_internal_token"):
        Settings()


@pytest.mark.parametrize("cost", ["-1", "5.405", "five"])
def test_monthly_cost_must_be_euros_and_cents(monkeypatch: pytest.MonkeyPatch, cost: str) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/observer")
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/1")
    monkeypatch.setenv("NEXTCLOUD_OBSERVER_PASSWORD", "observer-password")
    monkeypatch.setenv("OBSERVER_INTERNAL_TOKEN", "observer-token")
    monkeypatch.setenv("MONTHLY_COST_EUR", cost)

    with pytest.raises(ValueError, match="monthly_cost_eur"):
        Settings()
