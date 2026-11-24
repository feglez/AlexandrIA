import httpx
import pytest
import respx

from librarian.library import LibraryError, LibraryFiles, TooLarge
from tests.conftest import NEXTCLOUD, basic_auth

DAV = f"{NEXTCLOUD}/remote.php/dav/files/alexandria"

# Nextcloud 35's answer to a PROPFIND with Depth: infinity (recorded, with the
# folder renamed to Library).
LISTING = (
    '<?xml version="1.0"?>\n<d:multistatus xmlns:d="DAV:" xmlns:s="http://sabredav.org/ns" '
    'xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">'
    "<d:response><d:href>/remote.php/dav/files/alexandria/Library/</d:href><d:propstat><d:prop>"
    "<oc:fileid>178</oc:fileid><d:resourcetype><d:collection/></d:resourcetype></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "<d:response><d:href>/remote.php/dav/files/alexandria/Library/loose.txt</d:href><d:propstat>"
    "<d:prop><oc:fileid>183</oc:fileid><d:resourcetype/></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "<d:response><d:href>/remote.php/dav/files/alexandria/Library/Manuals/</d:href><d:propstat>"
    "<d:prop><oc:fileid>179</oc:fileid><d:resourcetype><d:collection/></d:resourcetype></d:prop>"
    "<d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "<d:response><d:href>/remote.php/dav/files/alexandria/Library/Manuals/"
    "Garant%c3%ada%20lavadora%20(2).md</d:href><d:propstat><d:prop><oc:fileid>181</oc:fileid>"
    "<d:resourcetype/></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "<d:response><d:href>/remote.php/dav/files/alexandria/Library/Manuals/Old%20ones/</d:href>"
    "<d:propstat><d:prop><oc:fileid>180</oc:fileid><d:resourcetype><d:collection/>"
    "</d:resourcetype></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
    "<d:response><d:href>/remote.php/dav/files/alexandria/Library/Manuals/Old%20ones/"
    "a%2bb%26c.txt</d:href><d:propstat><d:prop><oc:fileid>182</oc:fileid><d:resourcetype/>"
    "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>"
)


@pytest.fixture
async def library() -> LibraryFiles:
    return LibraryFiles(httpx.AsyncClient(base_url=NEXTCLOUD), "alexandria", "secret", "Library")


@respx.mock
async def test_lists_the_files_and_collections_of_the_library(library: LibraryFiles) -> None:
    route = respx.route(method="PROPFIND", url=f"{DAV}/Library").mock(
        return_value=httpx.Response(207, text=LISTING)
    )

    listing = await library.listing()

    assert listing.files == {
        181: "Library/Manuals/Garantía lavadora (2).md",
        182: "Library/Manuals/Old ones/a+b&c.txt",
    }
    assert listing.collections == {"Manuals"}
    request = route.calls.last.request
    assert request.headers["Depth"] == "infinity"
    assert request.headers["Authorization"] == basic_auth("alexandria", "secret")
    assert b"<oc:fileid/>" in request.content


@respx.mock
@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (httpx.Response(404), "HTTP 404"),
        (
            httpx.Response(
                207,
                text="<d:multistatus xmlns:d='DAV:'><d:response>"
                "<d:href>/elsewhere/a.md</d:href></d:response></d:multistatus>",
            ),
            "unexpected path",
        ),
        (
            httpx.Response(
                207,
                text="<d:multistatus xmlns:d='DAV:'><d:response><d:href>"
                "/remote.php/dav/files/alexandria/Library/Manuals/a.md</d:href>"
                "</d:response></d:multistatus>",
            ),
            "no file id",
        ),
        (httpx.ConnectError("refused"), "ConnectError"),
    ],
    ids=["not found", "elsewhere", "no file id", "unreachable"],
)
async def test_a_listing_that_cannot_be_trusted_is_an_error(
    library: LibraryFiles, answer: httpx.Response | Exception, message: str
) -> None:
    respx.route(method="PROPFIND", url=f"{DAV}/Library").mock(side_effect=[answer])

    with pytest.raises(LibraryError, match=message):
        await library.listing()


@respx.mock
async def test_downloads_a_file(library: LibraryFiles) -> None:
    route = respx.get(f"{DAV}/Library/Manuals/Garant%C3%ADa%20(2).md").mock(
        return_value=httpx.Response(200, content=b"content")
    )

    assert await library.download("Library/Manuals/Garantía (2).md", 100) == b"content"
    assert route.calls.last.request.headers["Authorization"] == basic_auth("alexandria", "secret")


@respx.mock
async def test_a_missing_file_downloads_as_none(library: LibraryFiles) -> None:
    respx.get(f"{DAV}/Library/Manuals/a.md").mock(return_value=httpx.Response(404))

    assert await library.download("Library/Manuals/a.md", 100) is None


@respx.mock
async def test_a_download_stops_above_the_limit(library: LibraryFiles) -> None:
    respx.get(f"{DAV}/Library/Manuals/a.md").mock(
        return_value=httpx.Response(200, content=b"x" * 101)
    )

    with pytest.raises(TooLarge):
        await library.download("Library/Manuals/a.md", 100)


@respx.mock
@pytest.mark.parametrize(
    ("answer", "message"),
    [(httpx.Response(503), "HTTP 503"), (httpx.ReadTimeout("slow"), "ReadTimeout")],
)
async def test_a_failed_download_is_an_error(
    library: LibraryFiles, answer: httpx.Response | Exception, message: str
) -> None:
    respx.get(f"{DAV}/Library/Manuals/a.md").mock(side_effect=[answer])

    with pytest.raises(LibraryError, match=message):
        await library.download("Library/Manuals/a.md", 100)
