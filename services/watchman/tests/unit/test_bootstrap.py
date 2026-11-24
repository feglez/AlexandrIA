import json

import httpx
import pytest
import respx

from tests.conftest import basic_auth
from watchman.bootstrap import BootstrapError, BootstrapSettings, run

NC = "http://nextcloud.test"
DAV = f"{NC}/remote.php/dav/files/alexandria"
SHARES = f"{NC}/ocs/v2.php/apps/files_sharing/api/v1/shares"
SUBADMINS = f"{NC}/ocs/v2.php/cloud/users/observer/subadmins"
WEBHOOKS = f"{NC}/ocs/v2.php/apps/webhook_listeners/api/v1/webhooks"
HOOK_URL = "http://watchman:8001/internal/webhooks/nextcloud"
RENAMED = "OCP\\Files\\Events\\Node\\NodeRenamedEvent"
DELETED = "OCP\\Files\\Events\\Node\\NodeDeletedEvent"


def ocs(data: object) -> dict:
    return {"ocs": {"meta": {"status": "ok", "statuscode": 200}, "data": data}}


@pytest.fixture
def settings() -> BootstrapSettings:
    return BootstrapSettings(
        nextcloud_url=NC,
        nextcloud_admin_user="admin",
        nextcloud_admin_password="admin-password",
        nextcloud_service_password="service-password",
        library_collections="Manuals, Invoices",
        webhook_secret="webhook-secret",
    )


def mock_installed(groups: list[str] | None = None, users: list[str] | None = None) -> None:
    respx.get(f"{NC}/status.php").respond(json={"installed": True})
    respx.get(f"{NC}/ocs/v2.php/cloud/groups").respond(
        json=ocs({"groups": groups or ["admin", "members", "curators", "trusted"]})
    )
    respx.get(f"{NC}/ocs/v2.php/cloud/users").respond(
        json=ocs({"users": users or ["admin", "alexandria", "observer", "alice"]})
    )


@respx.mock
def test_first_run_creates_everything(settings: BootstrapSettings) -> None:
    mock_installed()
    mkcol = respx.route(method="MKCOL", url__startswith=DAV).respond(201)
    respx.get(SHARES).respond(json=ocs([]))
    create_share = respx.post(SHARES).respond(json=ocs({"id": "1"}))
    respx.get(SUBADMINS).respond(json=ocs([]))
    add_subadmin = respx.post(SUBADMINS).respond(json=ocs([]))
    list_webhooks = respx.get(WEBHOOKS).respond(json=ocs([]))
    register = respx.post(WEBHOOKS).respond(json=ocs({"id": 1}))

    run(settings)

    created = [call.request.url.path.removeprefix(DAV.removeprefix(NC)) for call in mkcol.calls]
    assert created == ["/Library", "/Library/Manuals", "/Library/Invoices", "/Quarantine"]
    assert dict(httpx.QueryParams(create_share.calls.last.request.content.decode())) == {
        "path": "/Library",
        "shareType": "1",
        "shareWith": "members",
        "permissions": "1",
    }
    assert create_share.calls.last.request.headers["Authorization"] == basic_auth(
        "alexandria", "service-password"
    )
    assert add_subadmin.calls.last.request.content == b"groupid=members"
    assert add_subadmin.calls.last.request.headers["Authorization"] == basic_auth(
        "admin", "admin-password"
    )
    assert list_webhooks.calls.last.request.url.params["uri"] == HOOK_URL
    listeners = [json.loads(call.request.content) for call in register.calls]
    assert listeners == [
        {
            "httpMethod": "POST",
            "uri": HOOK_URL,
            "event": event,
            "authMethod": "header",
            "authData": {"Authorization": "Bearer webhook-secret"},
        }
        for event in (RENAMED, DELETED)
    ]
    assert register.calls.last.request.headers["Authorization"] == basic_auth(
        "alexandria", "service-password"
    )


@respx.mock
def test_second_run_changes_nothing(settings: BootstrapSettings) -> None:
    mock_installed()
    respx.route(method="MKCOL", url__startswith=DAV).respond(405)
    respx.get(SHARES).respond(
        json=ocs([{"id": "1", "share_type": 1, "share_with": "members", "permissions": 1}])
    )
    create_share = respx.post(SHARES)
    respx.get(SUBADMINS).respond(json=ocs(["members"]))
    add_subadmin = respx.post(SUBADMINS)
    respx.get(WEBHOOKS).respond(
        json=ocs([{"id": 7, "event": RENAMED, "uri": HOOK_URL}, {"id": 8, "event": DELETED}])
    )
    register = respx.post(WEBHOOKS)
    update = respx.post(url__regex=rf"{WEBHOOKS}/\d+$").respond(json=ocs({"id": 7}))

    run(settings)

    assert not create_share.called
    assert not add_subadmin.called
    assert not register.called
    # The secret is sent again, in case it changed.
    assert [call.request.url.path.rsplit("/", 1)[-1] for call in update.calls] == ["7", "8"]
    assert json.loads(update.calls.last.request.content)["authData"] == {
        "Authorization": "Bearer webhook-secret"
    }


@respx.mock
def test_fails_when_the_hook_did_not_create_the_groups(settings: BootstrapSettings) -> None:
    mock_installed(groups=["admin", "members"])

    with pytest.raises(BootstrapError, match="curators, trusted"):
        run(settings)


@respx.mock
def test_fails_on_unexpected_webdav_answers(settings: BootstrapSettings) -> None:
    mock_installed()
    respx.route(method="MKCOL", url__startswith=DAV).respond(507)

    with pytest.raises(BootstrapError, match="MKCOL Library: HTTP 507"):
        run(settings)


@respx.mock
def test_fails_when_the_webhooks_cannot_be_registered(settings: BootstrapSettings) -> None:
    mock_installed()
    respx.route(method="MKCOL", url__startswith=DAV).respond(405)
    respx.get(SHARES).respond(
        json=ocs([{"id": "1", "share_type": 1, "share_with": "members", "permissions": 1}])
    )
    respx.get(SUBADMINS).respond(json=ocs(["members"]))
    respx.get(WEBHOOKS).respond(json=ocs([]))
    respx.post(WEBHOOKS).respond(403)

    with pytest.raises(BootstrapError, match=r"POST /ocs/v2\.php/apps/webhook_listeners/.*403"):
        run(settings)
