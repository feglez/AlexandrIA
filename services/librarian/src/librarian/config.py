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

    # How long a validated Nextcloud login is cached in Redis.
    auth_cache_seconds: int = 300
    curators_group: str = "curators"
    log_level: str = "INFO"

    # Embedding model, baked into the image (see the Dockerfile).
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # Redis stream of the domain events, and librarian's consumer group.
    events_stream: str = "alexandria.events"
    events_group: str = "librarian"

    # Service account that owns the Library folder: librarian reads the
    # files as this account.
    nextcloud_service_user: str = "alexandria"
    nextcloud_service_password: str
    library_folder: str = "Library"
    # Larger files are not indexed.
    max_file_bytes: int = 50 * 1024 * 1024
    # How often the index is compared with the files of the library, and how
    # soon to try again when Nextcloud does not answer.
    reconcile_seconds: float = 900.0
    reconcile_retry_seconds: float = 60.0
