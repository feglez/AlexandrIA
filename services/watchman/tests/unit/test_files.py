import httpx
import pytest
import respx

from tests.conftest import NEXTCLOUD, basic_auth
from watchman.files import FilesError, NextcloudFiles

ROOT = f"{NEXTCLOUD}/remote.php/dav/files/alexandria"


def multistatus(*hrefs: str) -> str:
    """PROPFIND answer as Nextcloud writes it; folders end with a slash."""
    responses = "".join(
        f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype>"
        f"{'<d:collection/>' if href.endswith('/') else ''}"
        "</d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat>"
        "</d:response>"
        for href in hrefs
    )
    return (
        '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" '
        f'xmlns:oc="http://owncloud.org/ns">{responses}</d:multistatus>'
    )


@pytest.fixture
async def files():
    async with httpx.AsyncClient(base_url=NEXTCLOUD) as client:
        yield NextcloudFiles(client, "alexandria", "service-password")


@respx.mock
async def test_list_folders(files: NextcloudFiles) -> None:
    base = "/remote.php/dav/files/alexandria/Library"
    route = respx.request("PROPFIND", f"{ROOT}/Library").respond(
        207,
        text=multistatus(
            f"{base}/",
            f"{base}/Taxes/",
            f"{base}/Manuals/",
            f"{base}/School%20reports/",
            f"{base}/readme.txt",
        ),
    )

    assert await files.list_folders("Library") == ["Manuals", "School reports", "Taxes"]
    sent = route.calls.last.request
    assert sent.headers["Depth"] == "1"
    assert sent.headers["Authorization"] == basic_auth("alexandria", "service-password")


@respx.mock
async def test_list_folders_error(files: NextcloudFiles) -> None:
    respx.request("PROPFIND", f"{ROOT}/Library").respond(404)

    with pytest.raises(FilesError, match="HTTP 404"):
        await files.list_folders("Library")


@respx.mock
async def test_upload_sends_the_length_and_quotes_the_path(files: NextcloudFiles) -> None:
    respx.request("MKCOL", f"{ROOT}/Quarantine/sub_1").respond(201)
    route = respx.put(f"{ROOT}/Quarantine/sub_1/Garant%C3%ADa%20%231.pdf").respond(201)

    async def content():
        yield b"%PDF-"
        yield b"1.7"

    await files.make_folder("Quarantine/sub_1")
    await files.upload("Quarantine/sub_1/Garantía #1.pdf", content(), 8)

    sent = route.calls.last.request
    assert sent.headers["Content-Length"] == "8"
    assert "Transfer-Encoding" not in sent.headers
    assert sent.content == b"%PDF-1.7"


@respx.mock
async def test_upload_error(files: NextcloudFiles) -> None:
    respx.put(f"{ROOT}/Quarantine/a.pdf").respond(507)

    async def content():
        yield b"x"

    with pytest.raises(FilesError, match="HTTP 507"):
        await files.upload("Quarantine/a.pdf", content(), 1)


@respx.mock
async def test_nextcloud_down(files: NextcloudFiles) -> None:
    respx.request("MKCOL", f"{ROOT}/Quarantine/sub_1").mock(
        side_effect=httpx.ConnectError("refused")
    )

    with pytest.raises(FilesError, match="ConnectError"):
        await files.make_folder("Quarantine/sub_1")


@respx.mock
async def test_delete_ignores_missing_files(files: NextcloudFiles) -> None:
    respx.delete(f"{ROOT}/Quarantine/sub_1").respond(404)

    await files.delete("Quarantine/sub_1")
