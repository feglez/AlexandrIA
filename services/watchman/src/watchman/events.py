"""Domain events published on the Redis stream (README section 4.3).

Each stream entry has one field, "event", with the envelope as JSON:
{"id", "type", "occurred_at", "actor", "data"}.
"""

import json
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from ulid import ULID


def envelope(event_type: str, actor: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(ULID()),
        "type": event_type,
        "occurred_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "actor": actor,
        "data": data,
    }


class EventPublisher:
    def __init__(self, redis: Redis, stream: str) -> None:
        self._redis = redis
        self._stream = stream

    async def publish(self, event_type: str, actor: str, data: dict[str, Any]) -> str:
        event = envelope(event_type, actor, data)
        await self._redis.xadd(self._stream, {"event": json.dumps(event)})
        return event["id"]
