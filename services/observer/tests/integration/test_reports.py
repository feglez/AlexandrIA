from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from observer.consumer import EventConsumer
from observer.ledger import period_of
from observer.resources import Resources
from tests.integration.conftest import ALICE, CAROL, created, drain, emit, published, snapshot

SEPTEMBER = datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 19, 17, 45, 2, tzinfo=UTC)
END_OF_OCTOBER = datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)
NOVEMBER = datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)


async def report(client: httpx.AsyncClient, period: str) -> dict:
    response = await client.get(f"/api/v1/reports/{period}", auth=CAROL)
    assert response.status_code == 200
    return response.json()


def member(user: str, storage: int, contributed: int, share: float, amount: float, **counts: int):
    return {
        "user": user,
        "storage_bytes": storage,
        "contributed_bytes": contributed,
        "share": share,
        "amount_eur": amount,
        "submissions": counts.get("submissions", 0),
        "searches": counts.get("searches", 0),
        "questions": counts.get("questions", 0),
    }


async def test_the_readme_example(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    # README section 5.4: alice has 8321499136 bytes of her own and
    # 1288490188 in the library, bob 11281069056 bytes in all.
    await snapshot(resources, "alice", 8_321_499_136, END_OF_OCTOBER)
    await snapshot(resources, "bob", 11_281_068_533, END_OF_OCTOBER)
    await emit(resources, "file.published", published("alice", 1_288_490_188), occurred_at=OCTOBER)
    await emit(resources, "file.published", published("bob", 523), occurred_at=OCTOBER)
    for _ in range(2):
        await emit(resources, "submission.created", created("alice"), occurred_at=OCTOBER)
    for _ in range(3):
        await emit(resources, "search.performed", {}, actor="alice", occurred_at=OCTOBER)
    await emit(resources, "question.answered", {}, actor="bob", occurred_at=OCTOBER)
    await drain(consumer)

    assert await report(client, "2026-10") == {
        "period": "2026-10",
        "monthly_cost_eur": 5.4,
        "split_rule": "proportional_to_storage",
        "members": [
            member("alice", 9_609_989_324, 1_288_490_188, 0.46, 2.48, submissions=2, searches=3),
            member("bob", 11_281_069_056, 523, 0.54, 2.92, questions=1),
        ],
    }


async def test_the_last_snapshot_before_the_end_of_the_month(
    client: httpx.AsyncClient, mocked: respx.MockRouter, resources: Resources
) -> None:
    await snapshot(resources, "alice", 100, SEPTEMBER)
    await snapshot(resources, "alice", 200, OCTOBER)
    await snapshot(resources, "alice", 300, END_OF_OCTOBER)
    await snapshot(resources, "alice", 999, NOVEMBER)
    # Nothing taken in October: the last one before its end counts.
    await snapshot(resources, "bob", 700, SEPTEMBER)

    october = await report(client, "2026-10")
    september = await report(client, "2026-09")

    assert [(m["user"], m["storage_bytes"]) for m in october["members"]] == [
        ("alice", 300),
        ("bob", 700),
    ]
    assert [(m["user"], m["storage_bytes"]) for m in september["members"]] == [
        ("alice", 100),
        ("bob", 700),
    ]
    assert await report(client, "2026-08") == {
        "period": "2026-08",
        "monthly_cost_eur": 5.4,
        "split_rule": "proportional_to_storage",
        "members": [],
    }


async def test_usage_of_the_month_and_contribution_up_to_its_end(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    await emit(resources, "file.published", published("alice", 1000), occurred_at=SEPTEMBER)
    await emit(resources, "submission.created", created("alice"), occurred_at=SEPTEMBER)
    await emit(resources, "file.published", published("alice", 500), occurred_at=OCTOBER)
    await emit(resources, "file.removed", published("alice", 200), occurred_at=OCTOBER)
    await emit(resources, "submission.created", created("alice"), occurred_at=OCTOBER)
    await emit(resources, "file.published", published("alice", 4000), occurred_at=NOVEMBER)
    await emit(resources, "submission.created", created("alice"), occurred_at=NOVEMBER)
    # Only searches: bob has no storage, so he pays nothing.
    await emit(resources, "search.performed", {}, actor="bob", occurred_at=OCTOBER)
    await drain(consumer)

    october = await report(client, "2026-10")

    assert october["members"] == [
        member("alice", 1300, 1300, 1.0, 5.4, submissions=1),
        member("bob", 0, 0, 0.0, 0.0, searches=1),
    ]


async def test_without_storage_the_cost_is_split_equally(
    client: httpx.AsyncClient,
    mocked: respx.MockRouter,
    resources: Resources,
    consumer: EventConsumer,
) -> None:
    await emit(resources, "search.performed", {}, actor="alice", occurred_at=OCTOBER)
    await snapshot(resources, "bob", 0, OCTOBER)
    await drain(consumer)

    october = await report(client, "2026-10")

    assert [(m["user"], m["share"], m["amount_eur"]) for m in october["members"]] == [
        ("alice", 0.5, 2.7),
        ("bob", 0.5, 2.7),
    ]


async def test_the_monthly_cost_comes_from_the_settings(
    client: httpx.AsyncClient, mocked: respx.MockRouter, resources: Resources
) -> None:
    resources.settings.monthly_cost_eur = Decimal("10.00")
    for user in ("carol", "alice", "bob"):
        await snapshot(resources, user, 7, OCTOBER)

    october = await report(client, "2026-10")

    assert october["monthly_cost_eur"] == 10.0
    # The rounding difference goes to the largest share, the first by name on a tie.
    assert [(m["user"], m["amount_eur"]) for m in october["members"]] == [
        ("alice", 3.34),
        ("bob", 3.33),
        ("carol", 3.33),
    ]


async def test_only_months_that_are_over(
    client: httpx.AsyncClient, mocked: respx.MockRouter
) -> None:
    now = datetime.now(UTC)
    for period in (period_of(now), f"{now.year + 1}-01"):
        response = await client.get(f"/api/v1/reports/{period}", auth=CAROL)

        assert response.status_code == 409
        problem = response.json()
        assert problem["type"] == "https://alexandria.test/problems/period-not-closed"
        assert period in problem["detail"]


async def test_only_curators(client: httpx.AsyncClient, mocked: respx.MockRouter) -> None:
    assert (await client.get("/api/v1/reports/2026-10", auth=ALICE)).status_code == 403
    assert (await client.get("/api/v1/reports/2026-10")).status_code == 401


@pytest.mark.parametrize("period", ["2026-13", "2026-00", "2026-1", "october", "2026-10-01"])
async def test_invalid_periods(
    client: httpx.AsyncClient, mocked: respx.MockRouter, period: str
) -> None:
    response = await client.get(f"/api/v1/reports/{period}", auth=CAROL)

    assert response.status_code == 422
