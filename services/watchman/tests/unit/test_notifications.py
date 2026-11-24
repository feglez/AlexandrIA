import json

import httpx
import pytest
import respx

from tests.conftest import NEXTCLOUD, basic_auth
from watchman import notifications
from watchman.db.models import Submission
from watchman.notifications import Notification, Notifier

ENDPOINT = f"{NEXTCLOUD}/ocs/v2.php/apps/notifications/api/v3/admin_notifications"


def submission(**fields: object) -> Submission:
    values = {
        "id": "sub_01JB8Z5Y0F",
        "filename": "warranty {2026}.pdf",
        "collection": "Manuals",
        "submitted_by": "alice",
        "library_path": "Library/Manuals/warranty {2026} (2).pdf",
        "file_id": 4182,
    }
    return Submission(**(values | fields))


def rendered(text: str, parameters: dict[str, dict[str, str]]) -> str:
    """The text a member reads: each placeholder replaced by its name."""
    for key, value in parameters.items():
        text = text.replace("{" + key + "}", value["name"])
    return text


def test_published_by_a_curator() -> None:
    notification = notifications.published(submission(), "carol", "https://alexandria.test/")

    assert notification.user == "alice"
    assert rendered(notification.subject, notification.subject_parameters) == (
        "Your file warranty {2026}.pdf is in the library"
    )
    assert rendered(notification.message, notification.message_parameters) == (
        "carol approved it, and it is now at Library/Manuals/warranty {2026} (2).pdf."
    )
    assert notification.subject_parameters["file"] == {
        "type": "highlight",
        "id": "sub_01JB8Z5Y0F",
        "name": "warranty {2026}.pdf",
        "link": "https://alexandria.test/f/4182",
    }


def test_published_automatically() -> None:
    notification = notifications.published(submission(), None, "https://alexandria.test")

    assert rendered(notification.message, notification.message_parameters) == (
        "It was approved automatically, and it is now at Library/Manuals/warranty {2026} (2).pdf."
    )


def test_rejected() -> None:
    notification = notifications.rejected(submission(), "carol", "Upside {down}.")

    assert notification.user == "alice"
    assert rendered(notification.subject, notification.subject_parameters) == (
        "Your file warranty {2026}.pdf was rejected"
    )
    assert rendered(notification.message, notification.message_parameters) == (
        "carol rejected it. Reason: Upside {down}."
    )
    assert "link" not in notification.subject_parameters["file"]


@pytest.mark.parametrize(
    "notification",
    [
        notifications.published(submission(), "carol", "https://alexandria.test"),
        notifications.published(submission(), None, "https://alexandria.test"),
        notifications.rejected(submission(), "carol", "x" * 1000),
    ],
    ids=["published", "automatic", "rejected"],
)
def test_templates_hold_no_member_text(notification: Notification) -> None:
    # Nextcloud never shows a notification whose text has braces that are
    # not placeholders, and limits the subject to 255 bytes.
    for text, parameters in [
        (notification.subject, notification.subject_parameters),
        (notification.message, notification.message_parameters),
    ]:
        for key in parameters:
            text = text.replace("{" + key + "}", "")
        assert "{" not in text
        assert "}" not in text
    assert len(notification.subject.encode()) <= 255


@pytest.fixture
async def notifier():
    async with httpx.AsyncClient(base_url=NEXTCLOUD) as client:
        yield Notifier(client, "alexandria", "service-password")


@respx.mock
async def test_send(notifier: Notifier) -> None:
    route = respx.post(f"{ENDPOINT}/alice%20smith").respond(
        json={"ocs": {"meta": {"statuscode": 200}, "data": {"id": 23}}}
    )
    notification = notifications.rejected(submission(submitted_by="alice smith"), "carol", "No.")

    assert await notifier.send(notification)

    request = route.calls.last.request
    assert request.url.params["format"] == "json"
    assert request.headers["Authorization"] == basic_auth("alexandria", "service-password")
    assert request.headers["OCS-APIRequest"] == "true"
    assert json.loads(request.content) == {
        "subject": "Your file {file} was rejected",
        "message": "carol rejected it. Reason: {reason}",
        "subjectParameters": {
            "file": {"type": "highlight", "id": "sub_01JB8Z5Y0F", "name": "warranty {2026}.pdf"}
        },
        "messageParameters": {"reason": {"type": "highlight", "id": "reason", "name": "No."}},
    }


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(400, json={"ocs": {"meta": {"statuscode": 400}, "data": {"error": "user"}}}),
        httpx.Response(503),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
    ids=["unknown-user", "unavailable", "down", "timeout"],
)
@respx.mock
async def test_send_never_raises(
    notifier: Notifier, answer: httpx.Response | Exception, caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(f"{ENDPOINT}/alice").mock(side_effect=[answer])

    assert not await notifier.send(notifications.rejected(submission(), "carol", "No."))

    assert "Cannot notify alice" in caplog.text
