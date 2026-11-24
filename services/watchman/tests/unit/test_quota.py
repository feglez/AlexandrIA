"""watchman's side of the quota-check contract with observer.

The example request and answer are in contracts/quota-check.json, which
observer's tests use too.
"""

import json
from pathlib import Path

import httpx
import pytest
import respx

from watchman.checks import QuotaAnswer
from watchman.quota import QuotaClient

CONTRACT = json.loads(
    (Path(__file__).parents[4] / "contracts" / "quota-check.json").read_text(encoding="utf-8")
)
OBSERVER = "http://observer.test:8001"
URL = OBSERVER + CONTRACT["request"]["path"]


@pytest.fixture
async def quota():
    async with httpx.AsyncClient(base_url=OBSERVER, timeout=2.0) as client:
        yield QuotaClient(client, "observer-token")


@respx.mock
async def test_request_matches_the_contract(quota: QuotaClient) -> None:
    expected = CONTRACT["request"]
    response = CONTRACT["response"]
    route = respx.route(method=expected["method"], url=URL).respond(
        response["status"], json=response["body"]
    )

    await quota.check(expected["body"]["user"], expected["body"]["size_bytes"])

    sent = route.calls.last.request
    assert json.loads(sent.content) == expected["body"]
    assert sent.headers["Authorization"] == "Bearer observer-token"
    assert sent.headers["Content-Type"] == expected["headers"]["Content-Type"]


@respx.mock
async def test_answer_from_the_contract(quota: QuotaClient) -> None:
    body = CONTRACT["response"]["body"]
    respx.post(URL).respond(CONTRACT["response"]["status"], json=body)

    answer = await quota.check("alice", 482133)

    assert answer == QuotaAnswer(
        allowed=body["allowed"],
        remaining_bytes=body["remaining_bytes"],
        remaining_submissions=body["remaining_submissions"],
    )


@respx.mock
async def test_quota_exceeded(quota: QuotaClient) -> None:
    body = CONTRACT["response"]["body"] | {"allowed": False, "remaining_submissions": 0}
    respx.post(URL).respond(200, json=body)

    answer = await quota.check("alice", 482133)

    assert answer is not None
    assert answer.allowed is False


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("too slow"),
        httpx.Response(404),
        httpx.Response(500),
        httpx.Response(200, json={"allowed": "maybe"}),
        httpx.Response(200, text="not json"),
    ],
    ids=["down", "timeout", "404", "500", "bad-field", "not-json"],
)
@respx.mock
async def test_no_answer(quota: QuotaClient, failure: Exception | httpx.Response) -> None:
    route = respx.post(URL)
    if isinstance(failure, Exception):
        route.mock(side_effect=failure)
    else:
        route.mock(return_value=failure)

    assert await quota.check("alice", 482133) is None
