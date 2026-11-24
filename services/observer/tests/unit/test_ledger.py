import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from observer.ledger import Entry, entry_from, period_of

AT = datetime(2026, 10, 19, 17, 45, 2, tzinfo=UTC)
FILE = {
    "submission_id": "sub_01JB8Z5Y0F",
    "path": "Library/Manuals/washing-machine-warranty.pdf",
    "collection": "Manuals",
    "file_id": 4182,
    "size_bytes": 482133,
    "mime_type": "application/pdf",
    "sha256": "9f2c1e",
    "submitted_by": "alice",
}


def event(event_type: str, data: dict, actor: str = "carol", **changes: object) -> str:
    envelope = {
        "id": "01JB8Z6M3Q7W2V5N9K4T0XR1HC",
        "type": event_type,
        "occurred_at": "2026-10-19T17:45:02Z",
        "actor": actor,
        "data": data,
    }
    return json.dumps(envelope | changes)


def submission(status: str) -> dict:
    return {"submission_id": "sub_01JB8Z5Y0F", "status": status, "submitted_by": "alice"}


@pytest.mark.parametrize(
    ("event_type", "data", "actor", "counts"),
    [
        # The bytes go to the member who submitted the file, not to the curator.
        ("file.published", FILE, "carol", {"member": "alice", "bytes": 482133}),
        ("file.published", FILE, "watchman", {"member": "alice", "bytes": 482133}),
        ("file.removed", FILE, "carol", {"member": "alice", "bytes": -482133}),
        ("submission.created", submission("pending_review"), "alice", {"submissions": 1}),
        ("submission.created", submission("approved"), "alice", {"submissions": 1}),
        ("search.performed", {"query": "warranty"}, "alice", {"searches": 1}),
        ("question.answered", {"question_id": "q_01JB9C4N2M"}, "alice", {"questions": 1}),
    ],
)
def test_events_that_count(event_type: str, data: dict, actor: str, counts: dict) -> None:
    entry = entry_from(event(event_type, data, actor))

    assert entry == Entry(
        event_id="01JB8Z6M3Q7W2V5N9K4T0XR1HC",
        event_type=event_type,
        occurred_at=AT,
        **{"member": "alice"} | counts,
    )


@pytest.mark.parametrize(
    ("event_type", "data"),
    [
        # Rejected by the checks: it never reached the library.
        ("submission.created", submission("rejected")),
        ("submission.decided", {"decision": "approved", "submitted_by": "alice"}),
        ("file.renamed", FILE),
        ("something.new", {}),
    ],
)
def test_events_that_count_nothing(event_type: str, data: dict) -> None:
    assert entry_from(event(event_type, data)) is None


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        json.dumps(["a", "list"]),
        event("file.published", FILE, id=""),
        event("file.published", FILE, occurred_at="yesterday"),
        # A moment without a time zone could be in either month.
        event("file.published", FILE, occurred_at="2026-10-31T23:30:00"),
        event("file.published", {**FILE, "submitted_by": ""}),
        event("file.published", {**FILE, "size_bytes": -1}),
        event("file.removed", {"path": "Library/Manuals/a.pdf"}),
        event("submission.created", {"submitted_by": "alice"}),
        event("search.performed", {}, actor=""),
    ],
)
def test_invalid_events(raw: str) -> None:
    with pytest.raises(ValueError):
        entry_from(raw)


def test_the_period_is_the_month_in_utc() -> None:
    madrid = timezone(timedelta(hours=1))

    assert period_of(datetime(2026, 11, 1, 0, 30, tzinfo=madrid)) == "2026-10"
    assert period_of(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == "2026-12"
    entry = entry_from(event("search.performed", {}, "alice", occurred_at="2026-11-01T00:30+01:00"))
    assert entry is not None
    assert entry.period == "2026-10"
