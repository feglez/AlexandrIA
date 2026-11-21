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
