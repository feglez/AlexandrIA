"""Consumer of the domain events on the Redis stream (README section 4.3).

Every observer process reads the stream in the consumer group `observer`,
so each entry goes to one of them. Delivery is at least once: an entry is
acknowledged only after its ledger entry is committed. An entry that stays
unacknowledged for a while (its process died, or the database failed) is
claimed again by any process, and the ledger counts each event only once.
A new group starts at the first entry of the stream, so the events emitted
before observer was running are counted too.
"""

import asyncio
import logging
import socket
from typing import Any

from redis.exceptions import ResponseError

from observer import ledger
from observer.resources import Resources

logger = logging.getLogger(__name__)

BATCH = 50
# How long one read waits for new entries. It must stay below the Redis
# client's read timeout (5 s by default), or every idle read fails.
BLOCK_MS = 2_000
# An entry delivered this long ago and not acknowledged is claimed again.
CLAIM_IDLE_MS = 60_000
RETRY_SECONDS = 5.0


class EventConsumer:
    def __init__(
        self,
        resources: Resources,
        *,
        name: str | None = None,
        block_ms: int = BLOCK_MS,
        claim_idle_ms: int = CLAIM_IDLE_MS,
        retry_seconds: float = RETRY_SECONDS,
    ) -> None:
        self._resources = resources
        self._redis = resources.redis
        self._stream = resources.settings.events_stream
        self._group = resources.settings.events_group
        # One consumer per process; the container's host name is unique.
        self._name = name or socket.gethostname()
        self._block_ms = block_ms
        self._claim_idle_ms = claim_idle_ms
        self._retry_seconds = retry_seconds

    async def run(self) -> None:
        """Consume events until cancelled."""
        while True:
            try:
                await self.create_group()
                while True:
                    await self.consume(self._block_ms)
            except Exception:
                logger.exception("The event consumer failed, it tries again shortly")
                await asyncio.sleep(self._retry_seconds)

    async def create_group(self) -> None:
        """Create the stream and the consumer group if they do not exist."""
        try:
            await self._redis.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def consume(self, block_ms: int | None = None) -> int:
        """Handle one batch: entries left pending too long, or else new ones.

        Waits up to `block_ms` for new entries (None: does not wait).
        Returns the number of entries handled.
        """
        _, entries, *_ = await self._redis.xautoclaim(
            self._stream, self._group, self._name, self._claim_idle_ms, count=BATCH
        )
        if not entries:
            answer = await self._redis.xreadgroup(
                self._group, self._name, {self._stream: ">"}, count=BATCH, block=block_ms
            )
            entries = [entry for _, stream_entries in answer or [] for entry in stream_entries]
        for entry_id, fields in entries:
            await self._handle(entry_id, fields)
        return len(entries)

    async def _handle(self, entry_id: bytes, fields: dict[bytes, Any] | None) -> None:
        raw = (fields or {}).get(b"event")
        entry = None
        if raw is None:
            logger.warning("Skipping stream entry %s: it has no event field", entry_id.decode())
        else:
            try:
                entry = ledger.entry_from(raw)
            except ValueError as exc:
                logger.warning("Skipping stream entry %s: %s", entry_id.decode(), exc)
        if entry is not None:
            async with self._resources.sessions.begin() as session:
                await ledger.record(session, entry)
        await self._redis.xack(self._stream, self._group, entry_id)
