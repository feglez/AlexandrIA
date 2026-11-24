"""Snapshots of the members' personal storage, for the monthly report.

Every observer process takes a snapshot when it starts and then once a day
(STORAGE_SNAPSHOT_SECONDS): it lists the members group in Nextcloud and
stores the bytes each member uses in `storage_snapshots`. The report of a
month uses the last snapshot taken before the month ended, so a snapshot
taken twice, by two replicas, does no harm. A member whose storage Nextcloud
does not tell is left out of that snapshot. If the group cannot be listed,
the snapshot is tried again a minute later.
"""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from observer.db.models import StorageSnapshot
from observer.resources import Resources

logger = logging.getLogger(__name__)


class StorageSnapshots:
    def __init__(
        self,
        resources: Resources,
        *,
        interval_seconds: float | None = None,
        retry_seconds: float | None = None,
    ) -> None:
        settings = resources.settings
        self._resources = resources
        self._group = settings.members_group
        self._interval = interval_seconds or settings.storage_snapshot_seconds
        self._retry = retry_seconds or settings.storage_snapshot_retry_seconds

    async def run(self) -> None:
        """Take a snapshot now and then at every interval, until cancelled."""
        while True:
            try:
                taken = await self.take()
            except Exception:
                logger.exception("The storage snapshot failed")
                taken = None
            await asyncio.sleep(self._interval if taken is not None else self._retry)

    async def take(self) -> int | None:
        """Store the storage of every member now.

        Returns how many members were stored, or None if the members group
        could not be listed.
        """
        storage = self._resources.storage
        members = await storage.members(self._group)
        if members is None:
            return None
        taken_at = datetime.now(UTC)
        rows = []
        for member in members:
            usage = await storage.usage(member)
            if usage is not None:
                rows.append(dict(member=member, taken_at=taken_at, used_bytes=usage.used_bytes))
        if rows:
            async with self._resources.sessions.begin() as session:
                await session.execute(insert(StorageSnapshot).values(rows).on_conflict_do_nothing())
        logger.info("Recorded the storage of %d of %d members", len(rows), len(members))
        return len(rows)
