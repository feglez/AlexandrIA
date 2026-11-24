"""Usage ledger: what each domain event adds to a member's usage.

| Event | Member | Counts |
|---|---|---|
| `file.published` | `data.submitted_by` | + `data.size_bytes` contributed |
| `file.removed` | `data.submitted_by` | - `data.size_bytes` contributed |
| `submission.created` | `data.submitted_by` | 1 submission, unless its `status` is `rejected` |
| `search.performed` | `actor` | 1 search |
| `question.answered` | `actor` | 1 question |

The actor of `file.published` is the curator who approved the file, so the
bytes always go to `data.submitted_by`. Submissions rejected by the checks
never reach the library and are not counted. Other events count nothing.

Each event is counted in the month (UTC) of its `occurred_at`. It is stored
in `usage_events` under its id, and the totals in `usage_totals` change in
the same transaction only when that row is new, so an event delivered twice
is counted once.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from observer.db.models import UsageEvent, UsageTotal


class _Envelope(BaseModel):
    id: str = Field(min_length=1)
    type: str
    occurred_at: AwareDatetime
    actor: str
    data: dict[str, Any] = {}


class _FileData(BaseModel):
    submitted_by: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class _SubmissionData(BaseModel):
    submitted_by: str = Field(min_length=1)
    status: str


@dataclass(frozen=True)
class Entry:
    event_id: str
    event_type: str
    member: str
    occurred_at: datetime
    bytes: int = 0
    submissions: int = 0
    searches: int = 0
    questions: int = 0

    @property
    def period(self) -> str:
        return period_of(self.occurred_at)


def period_of(moment: datetime) -> str:
    """The month of a moment, "YYYY-MM" in UTC."""
    return moment.astimezone(UTC).strftime("%Y-%m")


def entry_from(raw: str | bytes) -> Entry | None:
    """The ledger entry of an event, or None if the event counts nothing.

    Raises ValueError if the event is not valid JSON or lacks a field it
    needs.
    """
    event = _Envelope.model_validate_json(raw)
    common = {"event_id": event.id, "event_type": event.type, "occurred_at": event.occurred_at}
    match event.type:
        case "file.published" | "file.removed":
            data = _FileData.model_validate(event.data)
            sign = 1 if event.type == "file.published" else -1
            return Entry(**common, member=data.submitted_by, bytes=sign * data.size_bytes)
        case "submission.created":
            created = _SubmissionData.model_validate(event.data)
            if created.status == "rejected":
                return None
            return Entry(**common, member=created.submitted_by, submissions=1)
        case "search.performed" | "question.answered":
            if not event.actor:
                raise ValueError(f"{event.type} {event.id} has no actor")
            counter = "searches" if event.type == "search.performed" else "questions"
            return Entry(**common, member=event.actor, **{counter: 1})
    return None


async def record(session: AsyncSession, entry: Entry) -> bool:
    """Store the entry and add it to the totals. False if it was already stored."""
    stored = await session.scalar(
        insert(UsageEvent)
        .values(
            event_id=entry.event_id,
            event_type=entry.event_type,
            member=entry.member,
            bytes=entry.bytes,
            occurred_at=entry.occurred_at,
        )
        .on_conflict_do_nothing(index_elements=[UsageEvent.event_id])
        .returning(UsageEvent.event_id)
    )
    if stored is None:
        return False
    totals = insert(UsageTotal).values(
        member=entry.member,
        period=entry.period,
        contributed_bytes=entry.bytes,
        submissions=entry.submissions,
        searches=entry.searches,
        questions=entry.questions,
    )
    await session.execute(
        totals.on_conflict_do_update(
            index_elements=[UsageTotal.member, UsageTotal.period],
            set_={
                column: getattr(UsageTotal, column) + getattr(totals.excluded, column)
                for column in ("contributed_bytes", "submissions", "searches", "questions")
            },
        )
    )
    return True
