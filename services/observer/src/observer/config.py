"""Settings read from environment variables."""

from decimal import Decimal

from pydantic import Field
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

    # Account that reads the members' storage (sub-admin of the members group).
    nextcloud_observer_user: str = "observer"
    nextcloud_observer_password: str

    # Redis stream of the domain events and observer's consumer group.
    events_stream: str = "alexandria.events"
    events_group: str = "observer"

    # Token that watchman sends with the quota check on the internal port.
    observer_internal_token: str

    # Contribution quota of a member who has no quota of their own.
    default_max_contribution_bytes: int = 5 * 1024**3
    default_max_submissions_per_month: int = 50

    # The members' personal storage is recorded at start-up and then every
    # storage_snapshot_seconds, for the monthly report. If the members group
    # cannot be read, it is tried again after storage_snapshot_retry_seconds.
    members_group: str = "members"
    storage_snapshot_seconds: float = 24 * 3600
    storage_snapshot_retry_seconds: float = 60

    # Running cost of the platform in euros a month, split among the members.
    monthly_cost_eur: Decimal = Field(default=Decimal("5.40"), ge=0, decimal_places=2)
