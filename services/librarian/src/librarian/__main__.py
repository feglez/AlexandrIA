"""Entry point: `python -m librarian`.

1. Apply the database migrations.
2. Load the embedding model.
3. Serve the API on PUBLIC_PORT. Scaling is done by running more containers.
"""

import asyncio
import logging

import uvicorn

from librarian.app import create_public_app
from librarian.config import Settings
from librarian.db.migrate import upgrade_database
from librarian.embeddings import Embedder, FastEmbedEmbedder
from librarian.resources import Resources

logger = logging.getLogger("librarian")


class _SkipHealthChecks(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


async def serve(settings: Settings, embedder: Embedder) -> None:
    resources = Resources.from_settings(settings, embedder)
    server = uvicorn.Server(
        uvicorn.Config(
            create_public_app(resources),
            host=settings.listen_host,
            port=settings.public_port,
            log_config=None,
            proxy_headers=False,
        )
    )
    try:
        await server.serve()
    finally:
        await resources.aclose()


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("uvicorn.access").addFilter(_SkipHealthChecks())
    logger.info("Applying database migrations")
    upgrade_database(settings.database_url)
    logger.info("Loading the embedding model %s", settings.embedding_model)
    embedder = FastEmbedEmbedder(settings.embedding_model)
    try:
        asyncio.run(serve(settings, embedder))
    finally:
        embedder.close()


if __name__ == "__main__":
    main()
