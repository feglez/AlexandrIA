import asyncio

import httpx
import pytest
import respx

from observer.resources import Resources
from observer.snapshots import StorageSnapshots
from tests.integration.conftest import STORAGE, snapshots


async def test_records_the_storage_of_every_member(
    resources: Resources, mocked: respx.MockRouter
) -> None:
    taken = await StorageSnapshots(resources).take()

    # carol's storage cannot be read, so she is left out.
    assert taken == 2
    assert await snapshots(resources) == [
        ("alice", STORAGE["alice"]["used"]),
        ("bob", STORAGE["bob"]["used"]),
    ]


async def test_each_snapshot_is_kept(resources: Resources, mocked: respx.MockRouter) -> None:
    await StorageSnapshots(resources).take()
    await StorageSnapshots(resources).take()

    assert [member for member, _ in await snapshots(resources)] == ["alice", "alice", "bob", "bob"]


async def test_nothing_is_stored_without_the_members(
    resources: Resources, mocked: respx.MockRouter
) -> None:
    mocked["members"].mock(return_value=httpx.Response(403))

    assert await StorageSnapshots(resources).take() is None
    assert await snapshots(resources) == []


async def test_run_tries_again_soon_after_a_failure(
    resources: Resources, mocked: respx.MockRouter
) -> None:
    mocked["members"].mock(
        side_effect=[httpx.Response(503), httpx.ConnectError("refused")]
        + [httpx.Response(200, json={"ocs": {"data": {"users": ["alice"]}}})] * 10
    )
    task = asyncio.create_task(
        StorageSnapshots(resources, interval_seconds=3600, retry_seconds=0.01).run()
    )

    for _ in range(100):
        if await snapshots(resources):
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Two failures, then one snapshot: the next one waits for the interval.
    assert mocked["members"].call_count == 3
    assert await snapshots(resources) == [("alice", STORAGE["alice"]["used"])]
