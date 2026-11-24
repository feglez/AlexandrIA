import httpx
import pytest
import respx

from observer.config import Settings
from observer.resources import nextcloud_client
from observer.storage import NextcloudStorage, Storage
from tests.conftest import NEXTCLOUD, basic_auth

ALICE_URL = f"{NEXTCLOUD}/ocs/v2.php/cloud/users/alice"


def details(quota: object) -> dict:
    return {"ocs": {"meta": {"statuscode": 200}, "data": {"id": "alice", "quota": quota}}}


@pytest.fixture
async def storage(settings: Settings):
    async with nextcloud_client(settings) as client:
        yield NextcloudStorage(client, "observer", "observer-password")


@pytest.mark.parametrize(
    ("quota", "expected"),
    [
        # As Nextcloud 35.0.3 answered for a member with no limit and with 1 GB.
        ({"free": -3, "used": 23, "total": -3, "relative": 0, "quota": -3}, Storage(23, None)),
        (
            {"free": 1073741301, "used": 523, "total": 1073741824, "quota": 1073741824},
            Storage(523, 1073741824),
        ),
        # Other forms the client also takes.
        ({"quota": "none", "used": 0}, Storage(0, None)),
        ({"quota": 5368709120.0, "used": 8321499136.0}, Storage(8321499136, 5368709120)),
    ],
)
@respx.mock
async def test_reads_used_bytes_and_quota(
    storage: NextcloudStorage, quota: dict, expected: Storage
) -> None:
    route = respx.get(ALICE_URL).respond(json=details(quota))

    assert await storage.usage("alice") == expected
    sent = route.calls.last.request
    assert sent.url.params["format"] == "json"
    assert sent.headers["OCS-APIRequest"] == "true"
    assert sent.headers["Authorization"] == basic_auth("observer", "observer-password")


@respx.mock
async def test_the_user_name_is_quoted(storage: NextcloudStorage) -> None:
    route = respx.get(f"{NEXTCLOUD}/ocs/v2.php/cloud/users/ana%20mar%C3%ADa%2F1").respond(
        json=details({"used": 1, "quota": -3})
    )

    assert await storage.usage("ana maría/1") == Storage(1, None)
    assert route.called


@pytest.mark.parametrize(
    "answer",
    [
        # Not a member: the observer account may not read the account.
        httpx.Response(404, json={"ocs": {"meta": {"statuscode": 998}, "data": []}}),
        httpx.Response(401),
        httpx.Response(500),
        httpx.Response(200, text="<html>maintenance</html>"),
        httpx.Response(200, json={"ocs": {"meta": {"statuscode": 200}, "data": []}}),
        httpx.Response(200, json=details({"used": 1})),
        httpx.Response(200, json=details({"used": "1", "quota": -3})),
        httpx.Response(200, json=details({"used": -1, "quota": -3})),
        httpx.Response(200, json=details({"used": True, "quota": -3})),
        # Space not computed (-1) or unknown (-2) is not "no limit".
        httpx.Response(200, json=details({"used": 1, "quota": -1})),
        httpx.Response(200, json=details({"used": 1, "quota": -2})),
        httpx.Response(200, json=details({"used": 1, "quota": "1 GB"})),
        httpx.Response(200, json=details(None)),
    ],
)
@respx.mock
async def test_unexpected_answers_give_nothing(
    storage: NextcloudStorage, answer: httpx.Response
) -> None:
    respx.get(ALICE_URL).mock(return_value=answer)

    assert await storage.usage("alice") is None


@respx.mock
async def test_nextcloud_down_gives_nothing(storage: NextcloudStorage) -> None:
    respx.get(ALICE_URL).mock(side_effect=httpx.ConnectError("refused"))

    assert await storage.usage("alice") is None
