from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import FastAPI

from tests.conftest import NEXTCLOUD, FakeRedis, basic_auth
from watchman.auth import CurrentCurator, CurrentUser, NextcloudAuthenticator
from watchman.config import Settings
from watchman.problems import Problem, install_problem_handlers
from watchman.resources import nextcloud_client

USER_URL = f"{NEXTCLOUD}/ocs/v2.php/cloud/user"


def ocs_user(user_id: str, groups: list[str]) -> dict:
    return {
        "ocs": {
            "meta": {"status": "ok", "statuscode": 200},
            "data": {"id": user_id, "displayname": user_id.title(), "groups": groups},
        }
    }


@pytest.fixture
async def client(settings: Settings):
    async with httpx.AsyncClient(base_url=NEXTCLOUD) as nextcloud:
        authenticator = NextcloudAuthenticator(nextcloud, FakeRedis(), cache_seconds=300)
        app = FastAPI()
        app.state.resources = SimpleNamespace(settings=settings, authenticator=authenticator)
        install_problem_handlers(app)

        @app.get("/me")
        async def me(user: CurrentUser) -> dict:
            return {"id": user.id, "groups": sorted(user.groups)}

        @app.get("/curators-only")
        async def curators_only(user: CurrentCurator) -> dict:
            return {"id": user.id}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@respx.mock
async def test_valid_credentials(client: httpx.AsyncClient) -> None:
    route = respx.get(USER_URL).respond(json=ocs_user("alice", ["members"]))

    response = await client.get("/me", auth=("alice", "app-password"))

    assert response.status_code == 200
    assert response.json() == {"id": "alice", "groups": ["members"]}
    sent = route.calls.last.request
    assert sent.headers["OCS-APIRequest"] == "true"
    assert sent.headers["Authorization"] == basic_auth("alice", "app-password")


@respx.mock
async def test_result_is_cached(client: httpx.AsyncClient) -> None:
    route = respx.get(USER_URL).respond(json=ocs_user("alice", ["members"]))

    await client.get("/me", auth=("alice", "app-password"))
    await client.get("/me", auth=("alice", "app-password"))

    assert route.call_count == 1


@respx.mock
async def test_cache_depends_on_the_password(client: httpx.AsyncClient) -> None:
    route = respx.get(USER_URL).mock(
        side_effect=[httpx.Response(200, json=ocs_user("alice", ["members"])), httpx.Response(401)]
    )

    assert (await client.get("/me", auth=("alice", "right"))).status_code == 200
    assert (await client.get("/me", auth=("alice", "wrong"))).status_code == 401
    assert route.call_count == 2


@respx.mock
async def test_invalid_credentials(client: httpx.AsyncClient) -> None:
    respx.get(USER_URL).respond(401)

    response = await client.get("/me", auth=("alice", "wrong"))

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")
    assert response.json()["status"] == 401


async def test_missing_credentials(client: httpx.AsyncClient) -> None:
    response = await client.get("/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


@respx.mock
async def test_nextcloud_down(client: httpx.AsyncClient) -> None:
    respx.get(USER_URL).mock(side_effect=httpx.ConnectError("refused"))

    response = await client.get("/me", auth=("alice", "app-password"))

    assert response.status_code == 503


@respx.mock
async def test_members_are_not_curators(client: httpx.AsyncClient) -> None:
    respx.get(USER_URL).respond(json=ocs_user("alice", ["members"]))

    response = await client.get("/curators-only", auth=("alice", "app-password"))

    assert response.status_code == 403


@respx.mock
async def test_curators(client: httpx.AsyncClient) -> None:
    respx.get(USER_URL).respond(json=ocs_user("carol", ["members", "curators"]))

    response = await client.get("/curators-only", auth=("carol", "app-password"))

    assert response.status_code == 200


@respx.mock
async def test_nextcloud_session_cookies_are_not_sent_back(settings: Settings) -> None:
    # Nextcloud answers a valid login with session cookies. Sent back, they
    # would make it accept the next request whatever its credentials.
    route = respx.get(USER_URL).mock(
        side_effect=[
            httpx.Response(
                200,
                json=ocs_user("alice", ["members"]),
                headers={"Set-Cookie": "nc_session_id=abc; path=/; HttpOnly"},
            ),
            httpx.Response(401),
        ]
    )
    async with nextcloud_client(settings) as nextcloud:
        authenticator = NextcloudAuthenticator(nextcloud, FakeRedis(), cache_seconds=300)
        await authenticator.authenticate("alice", "app-password")
        with pytest.raises(Problem):
            await authenticator.authenticate("alice", "wrong-password")

    assert "Cookie" not in route.calls.last.request.headers
