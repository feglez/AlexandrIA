import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from observer import ledger
from observer.consumer import BLOCK_MS, EventConsumer
from observer.db.models import UsageEvent
from observer.resources import Resources
from tests.integration.conftest import created, drain, emit, published, totals

OCTOBER = datetime(2026, 10, 19, 17, 45, 2, tzinfo=UTC)
NOVEMBER = datetime(2026, 11, 3, 9, 0, 0, tzinfo=UTC)


async def pending(resources: Resources) -> int:
    settings = resources.settings
    info = await resources.redis.xpending(settings.events_stream, settings.events_group)
    return info["pending"]


async def ledger_size(resources: Resources) -> int:
    async with resources.sessions() as session:
        return await session.scalar(select(func.count()).select_from(UsageEvent))


async def test_counts_each_event_for_its_member_and_month(
    resources: Resources, consumer: EventConsumer
) -> None:
    await emit(
        resources, "submission.created", created("alice"), actor="alice", occurred_at=OCTOBER
    )
    await emit(
        resources, "file.published", published("alice", 1000), actor="carol", occurred_at=OCTOBER
    )
    await emit(
        resources, "file.published", published("alice", 500), actor="watchman", occurred_at=NOVEMBER
    )
    await emit(
        resources, "file.removed", published("alice", 1000), actor="carol", occurred_at=NOVEMBER
    )
    await emit(resources, "submission.created", created("bob"), actor="bob", occurred_at=NOVEMBER)
    await emit(resources, "search.performed", {}, actor="bob", occurred_at=NOVEMBER)
    await emit(resources, "search.performed", {}, actor="bob", occurred_at=NOVEMBER)
    await emit(resources, "question.answered", {}, actor="alice", occurred_at=NOVEMBER)

    assert await drain(consumer) == 8

    assert await totals(resources) == {
        ("alice", "2026-10"): (1000, 1, 0, 0),
        ("alice", "2026-11"): (-500, 0, 0, 1),
        ("bob", "2026-11"): (0, 1, 2, 0),
    }
    assert await pending(resources) == 0


async def test_events_that_count_nothing_are_acknowledged(
    resources: Resources, consumer: EventConsumer
) -> None:
    await emit(resources, "submission.created", created("alice", "rejected"), actor="alice")
    await emit(resources, "submission.decided", {"decision": "approved"}, actor="carol")
    await emit(resources, "file.renamed", published("alice", 1000))

    assert await drain(consumer) == 3

    assert await totals(resources) == {}
    assert await ledger_size(resources) == 0
    assert await pending(resources) == 0


async def test_invalid_entries_are_skipped(resources: Resources, consumer: EventConsumer) -> None:
    stream = resources.settings.events_stream
    await resources.redis.xadd(stream, {"event": "not json"})
    await resources.redis.xadd(stream, {"something": "else"})
    await emit(resources, "file.published", {"submitted_by": "alice"})
    await emit(resources, "file.published", published("alice", 1000), occurred_at=NOVEMBER)

    assert await drain(consumer) == 4

    assert await totals(resources) == {("alice", "2026-11"): (1000, 0, 0, 0)}
    assert await pending(resources) == 0


async def test_an_event_delivered_twice_is_counted_once(
    resources: Resources, consumer: EventConsumer
) -> None:
    event_id = await emit(
        resources, "file.published", published("alice", 1000), occurred_at=NOVEMBER
    )
    await emit(
        resources,
        "file.published",
        published("alice", 1000),
        occurred_at=NOVEMBER,
        event_id=event_id,
    )

    assert await drain(consumer) == 2

    assert await totals(resources) == {("alice", "2026-11"): (1000, 0, 0, 0)}
    assert await ledger_size(resources) == 1


async def test_a_new_group_counts_the_earlier_events(
    resources: Resources, consumer: EventConsumer
) -> None:
    # The events were emitted before observer created its group.
    await emit(resources, "file.published", published("alice", 1000), occurred_at=NOVEMBER)
    await emit(resources, "file.published", published("bob", 20), occurred_at=NOVEMBER)

    await consumer.create_group()
    await consumer.create_group()  # the group exists: nothing changes
    assert await drain(consumer) == 2

    assert await totals(resources) == {
        ("alice", "2026-11"): (1000, 0, 0, 0),
        ("bob", "2026-11"): (20, 0, 0, 0),
    }


async def test_two_consumers_share_the_events(resources: Resources) -> None:
    first = EventConsumer(resources, name="first")
    second = EventConsumer(resources, name="second")
    for _ in range(60):
        await emit(resources, "search.performed", {}, actor="alice", occurred_at=NOVEMBER)

    await first.create_group()
    assert await first.consume() == 50
    assert await second.consume() == 10

    assert await totals(resources) == {("alice", "2026-11"): (0, 0, 60, 0)}


async def test_an_entry_that_failed_is_taken_again(
    resources: Resources, monkeypatch: pytest.MonkeyPatch
) -> None:
    await emit(resources, "file.published", published("alice", 1000), occurred_at=NOVEMBER)
    failing = EventConsumer(resources, name="failing", claim_idle_ms=60_000)
    await failing.create_group()

    async def database_down(*args: object) -> bool:
        raise SQLAlchemyError("connection refused")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "record", database_down)
        with pytest.raises(SQLAlchemyError):
            await failing.consume()
    assert await pending(resources) == 1

    # Not before it has been waiting for claim_idle_ms...
    assert await EventConsumer(resources, name="other", claim_idle_ms=60_000).consume() == 0
    # ...and then by any consumer.
    await asyncio.sleep(0.05)
    assert await EventConsumer(resources, name="other", claim_idle_ms=10).consume() == 1

    assert await totals(resources) == {("alice", "2026-11"): (1000, 0, 0, 0)}
    assert await pending(resources) == 0


async def test_an_idle_read_ends_before_the_client_times_out(
    resources: Resources, consumer: EventConsumer
) -> None:
    # The Redis client gives up on a read after its socket timeout.
    await consumer.create_group()

    assert await consumer.consume(BLOCK_MS) == 0


async def test_run_waits_for_new_events_and_stops_when_cancelled(resources: Resources) -> None:
    consumer = EventConsumer(resources, name="runner", block_ms=100)
    task = asyncio.create_task(consumer.run())
    await emit(resources, "file.published", published("alice", 1000), occurred_at=NOVEMBER)

    for _ in range(100):
        if await totals(resources):
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await totals(resources) == {("alice", "2026-11"): (1000, 0, 0, 0)}


async def test_run_keeps_going_after_a_failure(
    resources: Resources, caplog: pytest.LogCaptureFixture
) -> None:
    consumer = EventConsumer(resources, name="runner", block_ms=100, retry_seconds=0.05)
    task = asyncio.create_task(consumer.run())
    await asyncio.sleep(0.2)
    # The stream disappears while the consumer runs, and its group with it.
    await resources.redis.delete(resources.settings.events_stream)
    for _ in range(100):
        if "The event consumer failed" in caplog.text:
            break
        await asyncio.sleep(0.05)
    assert "The event consumer failed" in caplog.text
    await emit(resources, "file.published", published("alice", 1000), occurred_at=NOVEMBER)

    for _ in range(100):
        if await totals(resources):
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await totals(resources) == {("alice", "2026-11"): (1000, 0, 0, 0)}
