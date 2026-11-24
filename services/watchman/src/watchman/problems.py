"""Errors as RFC 9457 problem details (application/problem+json)."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

PROBLEM_JSON = "application/problem+json"

logger = logging.getLogger(__name__)


class Problem(Exception):
    """An error that is returned to the client as a problem details object.

    `slug` names the problem type: it becomes `<PUBLIC_BASE_URL>/problems/<slug>`.
    Without it the type is `about:blank` and the title is the HTTP status phrase.
    Extra keyword arguments are added as extension members.
    """

    def __init__(
        self,
        status: int,
        detail: str | None = None,
        *,
        slug: str | None = None,
        title: str | None = None,
        headers: dict[str, str] | None = None,
        **extensions: Any,
    ) -> None:
        super().__init__(detail or title or HTTPStatus(status).phrase)
        self.status = status
        self.detail = detail
        self.slug = slug
        self.title = title or HTTPStatus(status).phrase
        self.headers = headers
        self.extensions = extensions


def problem_response(request: Request, problem: Problem) -> JSONResponse:
    if problem.slug:
        base_url = request.app.state.resources.settings.public_base_url.rstrip("/")
        problem_type = f"{base_url}/problems/{problem.slug}"
    else:
        problem_type = "about:blank"
    body: dict[str, Any] = {
        "type": problem_type,
        "title": problem.title,
        "status": problem.status,
    }
    if problem.detail:
        body["detail"] = problem.detail
    body["instance"] = request.url.path
    body.update(problem.extensions)
    return JSONResponse(
        body, status_code=problem.status, headers=problem.headers, media_type=PROBLEM_JSON
    )


async def _handle_problem(request: Request, exc: Problem) -> JSONResponse:
    return problem_response(request, exc)


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if exc.detail != HTTPStatus(exc.status_code).phrase else None
    return problem_response(request, Problem(exc.status_code, detail, headers=exc.headers))


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return problem_response(
        request,
        Problem(
            422,
            "The request is not valid.",
            slug="validation-error",
            title="Validation error",
            errors=errors,
        ),
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return problem_response(request, Problem(500))


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Problem, _handle_problem)
    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
