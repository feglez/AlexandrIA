import httpx
import pytest
import respx

from tests.conftest import basic_auth
from watchman.bootstrap import BootstrapError, BootstrapSettings, run

NC = "http://nextcloud.test"
DAV = f"{NC}/remote.php/dav/files/alexandria"
SHARES = f"{NC}/ocs/v2.php/apps/files_sharing/api/v1/shares"
SUBADMINS = f"{NC}/ocs/v2.php/cloud/users/observer/subadmins"


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

    run(settings)

    assert not create_share.called
    assert not add_subadmin.called


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
