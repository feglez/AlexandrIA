"""FastAPI application. The API is served under /api/v1."""

from importlib.metadata import version

from fastapi import FastAPI

from librarian.api import health
from librarian.problems import install_problem_handlers
from librarian.resources import Resources


def create_public_app(resources: Resources) -> FastAPI:
    app = FastAPI(title="librarian", version=version("librarian"))
    app.state.resources = resources
    install_problem_handlers(app)
    app.include_router(health.router)
    return app
