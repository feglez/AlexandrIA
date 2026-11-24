from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from observer.config import Settings
from observer.problems import PROBLEM_JSON, Problem, install_problem_handlers


class Payload(BaseModel):
    reason: str


@pytest.fixture
def client(settings: Settings) -> httpx.AsyncClient:
    app = FastAPI()
    app.state.resources = SimpleNamespace(settings=settings)
    install_problem_handlers(app)

    @app.get("/duplicate")
    async def duplicate() -> None:
        raise Problem(
            422,
            "An identical file already exists in the library.",
            slug="duplicate-file",
            title="Duplicate file",
            existing_path="Library/Manuals/warranty.pdf",
        )

    @app.get("/forbidden")
    async def forbidden() -> None:
        raise HTTPException(403)

    @app.post("/validate")
    async def validate(payload: Payload) -> None:
        pass

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("unexpected")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_problem_with_type_and_extensions(client: httpx.AsyncClient) -> None:
    response = await client.get("/duplicate")

    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "https://alexandria.test/problems/duplicate-file",
        "title": "Duplicate file",
        "status": 422,
        "detail": "An identical file already exists in the library.",
        "instance": "/duplicate",
        "existing_path": "Library/Manuals/warranty.pdf",
    }


async def test_http_errors_become_problems(client: httpx.AsyncClient) -> None:
    response = await client.get("/forbidden")

    assert response.status_code == 403
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json() == {
        "type": "about:blank",
        "title": "Forbidden",
        "status": 403,
        "instance": "/forbidden",
    }


async def test_unknown_route_is_a_problem(client: httpx.AsyncClient) -> None:
    response = await client.get("/nowhere")

    assert response.status_code == 404
    assert response.json()["title"] == "Not Found"


async def test_validation_errors_list_the_fields(client: httpx.AsyncClient) -> None:
    response = await client.post("/validate", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == "https://alexandria.test/problems/validation-error"
    assert body["errors"][0]["loc"] == ["body", "reason"]


async def test_unexpected_errors_hide_the_details(client: httpx.AsyncClient) -> None:
    response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "instance": "/boom",
    }
