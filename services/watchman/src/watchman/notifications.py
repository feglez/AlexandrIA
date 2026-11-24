"""Notifications to members in Nextcloud (the bell in the web interface).

watchman sends them as the service account, which is a Nextcloud admin, with
the notifications app's admin_notifications endpoint (OCS, API v3). The texts
are fixed templates. Every value that comes from a member or a curator (file
name, path, reason) goes in a rich object parameter, because Nextcloud
accepts a notification whose plain text contains braces but never shows it.

Sending is best effort: a notification that Nextcloud does not take is
logged and not sent again. The change it reports stays as it is.
"""

import logging
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from watchman.db.models import Submission

logger = logging.getLogger(__name__)

ENDPOINT = "/ocs/v2.php/apps/notifications/api/v3/admin_notifications/"
TIMEOUT = 5.0

Parameters = dict[str, dict[str, str]]


@dataclass(frozen=True)
class Notification:
    user: str
    subject: str
    message: str
    subject_parameters: Parameters = field(default_factory=dict)
    message_parameters: Parameters = field(default_factory=dict)


def _highlight(key: str, name: str, link: str | None = None) -> dict[str, str]:
    value = {"type": "highlight", "id": key, "name": name}
    if link:
        value["link"] = link
    return value


def published(submission: Submission, curator: str | None, public_base_url: str) -> Notification:
    """The submission is in the library. `curator` is None when the policy approved it."""
    link = f"{public_base_url.rstrip('/')}/f/{submission.file_id}"
    if curator is None:
        message = "It was approved automatically, and it is now at {path}."
    else:
        message = f"{curator} approved it, and it is now at {{path}}."
    return Notification(
        user=submission.submitted_by,
        subject="Your file {file} is in the library",
        message=message,
        subject_parameters={"file": _highlight(submission.id, submission.filename, link)},
        message_parameters={"path": _highlight("path", submission.library_path or "", link)},
    )


def rejected(submission: Submission, curator: str, reason: str) -> Notification:
    return Notification(
        user=submission.submitted_by,
        subject="Your file {file} was rejected",
        message=f"{curator} rejected it. Reason: {{reason}}",
        subject_parameters={"file": _highlight(submission.id, submission.filename)},
        message_parameters={"reason": _highlight("reason", reason)},
    )


class Notifier:
    def __init__(self, client: httpx.AsyncClient, user: str, password: str) -> None:
        self._client = client
        self._auth = (user, password)

    async def send(self, notification: Notification) -> bool:
        """Send one notification. Never raises: False, after logging why, if
        Nextcloud did not take it."""
        body = {
            "subject": notification.subject,
            "message": notification.message,
            "subjectParameters": notification.subject_parameters,
            "messageParameters": notification.message_parameters,
        }
        try:
            response = await self._client.post(
                ENDPOINT + quote(notification.user, safe=""),
                params={"format": "json"},
                json=body,
                auth=self._auth,
                headers={"OCS-APIRequest": "true"},
                timeout=TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning("Cannot notify %s: %r", notification.user, exc)
            return False
        if response.status_code != 200:
            logger.warning(
                "Cannot notify %s: HTTP %s %s",
                notification.user,
                response.status_code,
                response.text[:200],
            )
            return False
        return True
