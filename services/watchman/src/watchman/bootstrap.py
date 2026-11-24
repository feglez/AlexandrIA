"""One-shot setup of Nextcloud for AlexandrIA: `python -m watchman.bootstrap`.

It runs after Nextcloud is installed and every time the stack starts, so
every step checks the current state first. It:

1. checks that the post-installation hook created the groups and accounts;
2. creates the Library folder, one folder per collection and the Quarantine
   folder, owned by the service account;
3. shares Library with the members group, read-only;
4. makes the observer account a sub-admin of the members group, so it can
   read their storage usage.
"""

import logging
import sys
import time
from urllib.parse import quote

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("watchman.bootstrap")

OCS_HEADERS = {"OCS-APIRequest": "true", "Accept": "application/json"}
READ_ONLY = 1
GROUP_SHARE = 1


class BootstrapSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    nextcloud_url: str = "http://nextcloud"
    nextcloud_admin_user: str
    nextcloud_admin_password: str
    nextcloud_service_user: str = "alexandria"
    nextcloud_service_password: str
    observer_user: str = "observer"
    library_collections: str = "Manuals,Invoices,Contracts,Medical,School,Taxes"
    library_folder: str = "Library"
    quarantine_folder: str = "Quarantine"
    members_group: str = "members"
    required_groups: str = "members,curators,trusted"

    @property
    def collections(self) -> list[str]:
        return [name.strip() for name in self.library_collections.split(",") if name.strip()]


class BootstrapError(Exception):
    pass


def _ocs_data(response: httpx.Response) -> object:
    if response.status_code != 200:
        raise BootstrapError(
            f"{response.request.method} {response.request.url.path}: HTTP {response.status_code}"
        )
    return response.json()["ocs"]["data"]


def wait_for_nextcloud(client: httpx.Client, attempts: int = 30, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            response = client.get("/status.php")
            if response.status_code == 200 and response.json().get("installed"):
                return
        except (httpx.HTTPError, ValueError):
            pass
        logger.info("Waiting for Nextcloud (%d/%d)", attempt, attempts)
        time.sleep(delay)
    raise BootstrapError("Nextcloud is not installed or not reachable")


def check_accounts(admin: httpx.Client, settings: BootstrapSettings) -> None:
    groups = set(_ocs_data(admin.get("/ocs/v2.php/cloud/groups"))["groups"])
    missing = {g.strip() for g in settings.required_groups.split(",")} - groups
    if missing:
        raise BootstrapError(f"Missing groups: {', '.join(sorted(missing))}")
    users = set(_ocs_data(admin.get("/ocs/v2.php/cloud/users"))["users"])
    for user in (settings.nextcloud_service_user, settings.observer_user):
        if user not in users:
            raise BootstrapError(f"Missing account: {user}")
    logger.info("Groups and accounts are in place")


def create_folders(service: httpx.Client, settings: BootstrapSettings) -> None:
    root = f"/remote.php/dav/files/{quote(settings.nextcloud_service_user)}"
    folders = [settings.library_folder]
    folders += [f"{settings.library_folder}/{name}" for name in settings.collections]
    folders.append(settings.quarantine_folder)
    for folder in folders:
        response = service.request("MKCOL", f"{root}/{quote(folder)}")
        if response.status_code == 201:
            logger.info("Created folder %s", folder)
        elif response.status_code == 405:  # already exists
            logger.info("Folder %s already exists", folder)
        else:
            raise BootstrapError(f"MKCOL {folder}: HTTP {response.status_code}")


def share_library(service: httpx.Client, settings: BootstrapSettings) -> None:
    path = f"/{settings.library_folder}"
    shares_url = "/ocs/v2.php/apps/files_sharing/api/v1/shares"
    shares = _ocs_data(service.get(shares_url, params={"path": path}))
    for share in shares:
        if share["share_type"] == GROUP_SHARE and share["share_with"] == settings.members_group:
            if share["permissions"] != READ_ONLY:
                _ocs_data(
                    service.put(f"{shares_url}/{share['id']}", data={"permissions": READ_ONLY})
                )
                logger.info("Made the %s share read-only", path)
            else:
                logger.info("%s is already shared with %s", path, settings.members_group)
            return
    _ocs_data(
        service.post(
            shares_url,
            data={
                "path": path,
                "shareType": GROUP_SHARE,
                "shareWith": settings.members_group,
                "permissions": READ_ONLY,
            },
        )
    )
    logger.info("Shared %s with %s (read-only)", path, settings.members_group)


def grant_observer(admin: httpx.Client, settings: BootstrapSettings) -> None:
    url = f"/ocs/v2.php/cloud/users/{quote(settings.observer_user)}/subadmins"
    if settings.members_group in _ocs_data(admin.get(url)):
        logger.info("%s is already sub-admin of %s", settings.observer_user, settings.members_group)
        return
    _ocs_data(admin.post(url, data={"groupid": settings.members_group}))
    logger.info("Made %s sub-admin of %s", settings.observer_user, settings.members_group)


def run(settings: BootstrapSettings) -> None:
    common = {"base_url": settings.nextcloud_url, "headers": OCS_HEADERS, "timeout": 30.0}
    admin_auth = (settings.nextcloud_admin_user, settings.nextcloud_admin_password)
    service_auth = (settings.nextcloud_service_user, settings.nextcloud_service_password)
    with (
        httpx.Client(auth=admin_auth, **common) as admin,
        httpx.Client(auth=service_auth, **common) as service,
    ):
        wait_for_nextcloud(admin)
        check_accounts(admin, settings)
        create_folders(service, settings)
        share_library(service, settings)
        grant_observer(admin, settings)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        run(BootstrapSettings())
    except (BootstrapError, httpx.HTTPError) as exc:
        logger.error("Bootstrap failed: %s", exc)
        sys.exit(1)
    logger.info("Bootstrap finished")


if __name__ == "__main__":
    main()
