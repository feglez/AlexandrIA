"""Files of the service account in Nextcloud, over WebDAV.

The service account owns the Library folder (shared read-only with the
members) and the Quarantine folder (shared with nobody). Paths are relative
to its home folder, for example "Quarantine/sub_01JB.../report.pdf".
"""

import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

logger = logging.getLogger(__name__)

DAV = "{DAV:}"
OC = "{http://owncloud.org/ns}"
PROPFIND_BODY = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/></d:prop></d:propfind>'
)
FILE_ID_BODY = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    "<d:prop><oc:fileid/></d:prop></d:propfind>"
)
UPLOAD_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class FilesError(Exception):
    """Nextcloud did not do what was asked."""


class NextcloudFiles:
    def __init__(self, client: httpx.AsyncClient, user: str, password: str) -> None:
        self._client = client
        self._auth = (user, password)
        self._root = f"/remote.php/dav/files/{quote(user)}"

    def url(self, path: str) -> str:
        return f"{self._root}/{quote(path.strip('/'))}"

    async def list_folders(self, path: str) -> list[str]:
        """Names of the folders directly inside `path`, sorted."""
        response = await self._request(
            "PROPFIND",
            path,
            content=PROPFIND_BODY,
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        if response.status_code != 207:
            raise FilesError(f"PROPFIND {path}: HTTP {response.status_code}")
        base = unquote(self.url(path)).rstrip("/")
        names = []
        # The XML comes from our own Nextcloud, and Python's expat parser
        # refuses external entities and limits entity expansion.
        root = ElementTree.fromstring(response.content)  # noqa: S314
        for item in root.iter(f"{DAV}response"):
            href = unquote(urlsplit(item.findtext(f"{DAV}href", "")).path).rstrip("/")
            if href == base or item.find(f".//{DAV}collection") is None:
                continue
            names.append(href.rsplit("/", 1)[-1])
        return sorted(names)

    async def file_id(self, path: str) -> int | None:
        """Nextcloud's id of the file at `path`, or None if there is no such file.

        The id stays the same when the file is moved.
        """
        response = await self._request(
            "PROPFIND",
            path,
            content=FILE_ID_BODY,
            headers={"Depth": "0", "Content-Type": "application/xml"},
        )
        if response.status_code == 404:
            return None
        if response.status_code != 207:
            raise FilesError(f"PROPFIND {path}: HTTP {response.status_code}")
        root = ElementTree.fromstring(response.content)  # noqa: S314 - see list_folders
        value = root.findtext(f".//{OC}fileid", "")
        if not value.isdigit():
            raise FilesError(f"PROPFIND {path}: no file id in the answer")
        return int(value)

    async def move(self, source: str, target: str) -> bool:
        """Move a file on the server without overwriting anything.

        Returns False, and moves nothing, if `target` already exists.
        """
        destination = str(self._client.base_url).rstrip("/") + self.url(target)
        response = await self._request(
            "MOVE", source, headers={"Destination": destination, "Overwrite": "F"}
        )
        if response.status_code == 412:
            return False
        if response.status_code not in (201, 204):
            raise FilesError(f"MOVE {source} to {target}: HTTP {response.status_code}")
        return True

    async def make_folder(self, path: str) -> None:
        response = await self._request("MKCOL", path)
        if response.status_code != 201:
            raise FilesError(f"MKCOL {path}: HTTP {response.status_code}")

    async def upload(self, path: str, content: AsyncIterator[bytes], size_bytes: int) -> None:
        response = await self._request(
            "PUT",
            path,
            content=content,
            # With an explicit length httpx does not use chunked encoding.
            headers={"Content-Length": str(size_bytes)},
            timeout=UPLOAD_TIMEOUT,
        )
        if response.status_code not in (201, 204):
            raise FilesError(f"PUT {path}: HTTP {response.status_code}")

    async def delete(self, path: str) -> None:
        response = await self._request("DELETE", path)
        if response.status_code not in (204, 404):
            raise FilesError(f"DELETE {path}: HTTP {response.status_code}")

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, self.url(path), auth=self._auth, **kwargs)
        except httpx.HTTPError as exc:
            raise FilesError(f"{method} {path}: {exc!r}") from exc
