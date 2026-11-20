"""FastAPI applications.

The public app serves the API under /api/v1. The internal app listens on a
second port that is never published, for calls from other components.
"""

from importlib.metadata import version

from fastapi import FastAPI

from watchman.api import health
from watchman.problems import install_problem_handlers
from watchman.resources import Resources


def create_public_app(resources: Resources) -> FastAPI:
    app = FastAPI(title="watchman", version=version("watchman"))
    app.state.resources = resources
    install_problem_handlers(app)
    app.include_router(health.router)
    return app


def create_internal_app(resources: Resources) -> FastAPI:
    app = FastAPI(title="watchman internal API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.resources = resources
    install_problem_handlers(app)
    return app
