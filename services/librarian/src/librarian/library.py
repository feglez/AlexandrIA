"""The files of the library in Nextcloud, read over WebDAV as the service
account, which owns the Library folder.

Paths are relative to the account's home folder, for example
"Library/Manuals/washing-machine.pdf". A collection is a folder directly
inside Library; files directly inside Library are not part of the library.
"""

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree

import httpx

DAV = "{DAV:}"
OC = "{http://owncloud.org/ns}"
LISTING_BODY = (
    '<?xml version="1.0"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
    "<d:prop><oc:fileid/><d:resourcetype/></d:prop></d:propfind>"
)
LISTING_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


class LibraryError(Exception):
    """Nextcloud did not answer as expected."""


class TooLarge(Exception):
    """The file is larger than the limit, so it is not indexed."""


@dataclass(frozen=True)
class Listing:
    # Nextcloud file id -> path of every file in a collection.
    files: dict[int, str]
    collections: frozenset[str]


def collection_of(path: str) -> str:
    """The collection of a path in the library: "Library/Manuals/a.pdf" -> "Manuals"."""
    return path.split("/")[1]


class LibraryFiles:
    def __init__(self, client: httpx.AsyncClient, user: str, password: str, folder: str) -> None:
        self._client = client
        self._auth = (user, password)
        self._root = f"/remote.php/dav/files/{quote(user)}"
        self._folder = folder.strip("/")

    def url(self, path: str) -> str:
        return f"{self._root}/{quote(path.strip('/'))}"

    async def listing(self) -> Listing:
        """Every file and collection of the library, in one PROPFIND."""
        try:
            response = await self._client.request(
                "PROPFIND",
                self.url(self._folder),
                content=LISTING_BODY,
                headers={"Depth": "infinity", "Content-Type": "application/xml"},
                auth=self._auth,
                timeout=LISTING_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise LibraryError(f"PROPFIND {self._folder}: {exc!r}") from exc
        if response.status_code != 207:
            raise LibraryError(f"PROPFIND {self._folder}: HTTP {response.status_code}")

        files: dict[int, str] = {}
        collections = set()
        prefix = unquote(self._root) + "/"
        # The XML comes from our own Nextcloud, and Python's expat parser
        # refuses external entities and limits entity expansion.
        root = ElementTree.fromstring(response.content)  # noqa: S314
        for item in root.iter(f"{DAV}response"):
            href = unquote(urlsplit(item.findtext(f"{DAV}href", "")).path)
            if not href.startswith(prefix):
                raise LibraryError(f"PROPFIND {self._folder}: unexpected path {href}")
            path = href.removeprefix(prefix).rstrip("/")
            parts = path.split("/")
            if parts[0] != self._folder or len(parts) < 2:
                continue
            if item.find(f".//{DAV}collection") is not None:
                if len(parts) == 2:
                    collections.add(parts[1])
            elif len(parts) > 2:
                file_id = item.findtext(f".//{OC}fileid", "")
                if not file_id.isdigit():
                    raise LibraryError(f"PROPFIND {self._folder}: no file id for {path}")
                files[int(file_id)] = path
        return Listing(files=files, collections=frozenset(collections))

    async def download(self, path: str, max_bytes: int) -> bytes | None:
        """The content of the file, or None if there is no such file.

        Raises TooLarge as soon as more than `max_bytes` arrive.
        """
        try:
            async with self._client.stream(
                "GET", self.url(path), auth=self._auth, timeout=DOWNLOAD_TIMEOUT
            ) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise LibraryError(f"GET {path}: HTTP {response.status_code}")
                content = bytearray()
                async for part in response.aiter_bytes():
                    content += part
                    if len(content) > max_bytes:
                        raise TooLarge(f"{path} is larger than {max_bytes} bytes")
                return bytes(content)
        except httpx.HTTPError as exc:
            raise LibraryError(f"GET {path}: {exc!r}") from exc
