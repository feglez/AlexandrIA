"""Entry point: `python -m librarian`.

1. Apply the database migrations.
2. Load the embedding model.
3. Serve the API on PUBLIC_PORT, and run the consumer of the domain events,
   the indexer and the reconciliation with the library. Scaling is done by
   running more containers.
"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterator

import uvicorn

from librarian.app import create_public_app
from librarian.config import Settings
from librarian.consumer import EventConsumer
from librarian.db.migrate import upgrade_database
from librarian.embeddings import Embedder, FastEmbedEmbedder
from librarian.indexer import Indexer
from librarian.reconcile import Reconciler
from librarian.resources import Resources

logger = logging.getLogger("librarian")


class _Server(uvicorn.Server):
    # Signals are handled in serve(), so the tasks are stopped too.
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class _SkipHealthChecks(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


async def serve(settings: Settings, embedder: Embedder) -> None:
    resources = Resources.from_settings(settings, embedder)
    server = _Server(
        uvicorn.Config(
            create_public_app(resources),
            host=settings.listen_host,
            port=settings.public_port,
            log_config=None,
            proxy_headers=False,
        )
    )

    def stop() -> None:
        server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop)
    tasks = [
        asyncio.create_task(EventConsumer(resources).run(), name="consumer"),
        asyncio.create_task(Indexer(resources).run(), name="indexer"),
        asyncio.create_task(Reconciler(resources).run(), name="reconciler"),
    ]
    try:
        await server.serve()
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
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
