"""Entry point: `python -m watchman`.

1. Apply the database migrations.
2. Serve the public API (PUBLIC_PORT) and the internal API (INTERNAL_PORT)
   from the same process. Scaling is done by running more containers.
"""

import asyncio
import contextlib
import logging
import signal
from collections.abc import Iterator

import uvicorn

from watchman.app import create_internal_app, create_public_app
from watchman.config import Settings
from watchman.db.migrate import upgrade_database
from watchman.resources import Resources

logger = logging.getLogger("watchman")


class _Server(uvicorn.Server):
    # Signals are handled once for both servers in serve().
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class _SkipHealthChecks(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "/healthz" not in record.getMessage()


async def serve(settings: Settings) -> None:
    resources = Resources.from_settings(settings)
    servers = [
        _Server(
            uvicorn.Config(
                app,
                host=settings.listen_host,
                port=port,
                log_config=None,
                proxy_headers=False,
            )
        )
        for app, port in (
            (create_public_app(resources), settings.public_port),
            (create_internal_app(resources), settings.internal_port),
        )
    ]

    def stop() -> None:
        for server in servers:
            server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop)
    try:
        await asyncio.gather(*(server.serve() for server in servers))
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
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
