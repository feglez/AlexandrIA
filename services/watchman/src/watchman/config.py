"""Settings read from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str
    redis_url: str
    nextcloud_url: str = "http://nextcloud"
    # Base of the links to Nextcloud and of the problem type URIs.
    public_base_url: str = "http://localhost:8080"

    listen_host: str = "0.0.0.0"  # noqa: S104 - inside a container
    public_port: int = 8000
    internal_port: int = 8001

    # How long a validated Nextcloud login is cached in Redis.
    auth_cache_seconds: int = 300
    curators_group: str = "curators"
    log_level: str = "INFO"

    # Service account that owns the Library and Quarantine folders.
    nextcloud_service_user: str = "alexandria"
    nextcloud_service_password: str
    library_folder: str = "Library"
    quarantine_folder: str = "Quarantine"

    # observer's internal API, used for the quota check.
    observer_internal_url: str = "http://observer:8001"
    observer_internal_token: str
    quota_check_timeout_seconds: float = 2.0

    # Redis stream of the domain events.
    events_stream: str = "alexandria.events"
