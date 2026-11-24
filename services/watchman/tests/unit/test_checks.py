import hashlib
import io

import pytest

from tests import samples
from watchman import checks
from watchman.checks import Check, FileFacts, QuotaAnswer
from watchman.policy import DEFAULT_POLICY, DOCX

POLICY = DEFAULT_POLICY


def facts(size_bytes: int = 1000, mime_type: str = "application/pdf") -> FileFacts:
    return FileFacts(size_bytes=size_bytes, sha256="0" * 64, mime_type=mime_type)


@pytest.mark.parametrize(
    ("content", "filename", "mime_type"),
    [
        (samples.PDF, "warranty.pdf", "application/pdf"),
        (samples.PNG, "photo.png", "image/png"),
        (samples.JPEG, "photo.JPG", "image/jpeg"),
        (samples.DOCX, "letter.docx", DOCX),
        (samples.DOCX_LATE_PART, "letter.docx", DOCX),
        (samples.XLSX, "budget.xlsx", checks.EXTENSION_TYPES[".xlsx"]),
        (samples.zip_archive(), "photos.zip", "application/zip"),
        (samples.TEXT, "notes.txt", "text/plain"),
        (samples.MARKDOWN, "notes.md", "text/markdown"),
        (b'{"a": 1}', "data.json", "application/json"),
        # Text with an extension that is not a text type.
        (samples.TEXT, "notes.pdf", "text/plain"),
        # A PDF renamed to .txt is not valid UTF-8.
        (samples.PDF + b"\xff\xfe", "notes.txt", "application/pdf"),
        (samples.EXE, "setup.exe", "application/x-msdownload"),
        (b"\x00\x01\x02\x03binary", "data.bin", "application/octet-stream"),
        (b"\xc3\x28 invalid utf-8", "notes.txt", "application/octet-stream"),
    ],
)
def test_sniffed_mime_type(content: bytes, filename: str, mime_type: str) -> None:
    assert checks.sniff_mime_type(io.BytesIO(content), filename) == mime_type


def test_inspect_file_reads_size_and_sha256() -> None:
    content = samples.PDF * 100_000  # several chunks

    result = checks.inspect_file(io.BytesIO(content), "big.pdf")

    assert result == FileFacts(len(content), hashlib.sha256(content).hexdigest(), "application/pdf")


@pytest.mark.parametrize(
    ("size_bytes", "result"),
    [(1, "pass"), (POLICY.max_size_bytes, "pass"), (POLICY.max_size_bytes + 1, "fail")],
)
def test_size_limit(size_bytes: int, result: str) -> None:
    assert checks.size_limit(facts(size_bytes=size_bytes), POLICY).result == result


def test_size_limit_detail() -> None:
    check = checks.size_limit(facts(size_bytes=52_428_809), POLICY)

    assert check.detail == (
        "The file is 52,428,809 bytes, over the limit of 52,428,800 bytes (52.4 MB)."
    )


@pytest.mark.parametrize(
    ("filename", "mime_type", "result"),
    [
        ("warranty.pdf", "application/pdf", "pass"),
        ("WARRANTY.PDF", "application/pdf", "pass"),
        ("photo.jpeg", "image/jpeg", "pass"),
        ("letter.docx", DOCX, "pass"),
        ("notes.md", "text/markdown", "pass"),
        ("photo.png", "application/pdf", "fail"),
        ("warranty.pdf", "text/plain", "fail"),
        ("letter.docx", "application/zip", "fail"),
        ("archive.xyz", "application/octet-stream", "fail"),
        ("README", "text/plain", "fail"),
    ],
)
def test_mime_consistency(filename: str, mime_type: str, result: str) -> None:
    assert checks.mime_consistency(facts(mime_type=mime_type), filename).result == result


def test_mime_consistency_detail() -> None:
    check = checks.mime_consistency(facts(mime_type="text/plain"), "warranty.pdf")

    assert check.detail == "The content is text/plain, but .pdf means application/pdf."


@pytest.mark.parametrize(
    ("filename", "mime_type", "result"),
    [
        ("warranty.pdf", "application/pdf", "pass"),
        ("notes.md", "text/markdown", "pass"),
        ("notes.txt", "text/plain", "pass"),
        ("letter.docx", DOCX, "pass"),
        ("setup.exe", "application/x-msdownload", "fail"),
        ("install.SH", "text/plain", "fail"),
        ("photo.gif", "image/gif", "fail"),
        ("photos.zip", "application/zip", "fail"),
    ],
)
def test_blocked_type(filename: str, mime_type: str, result: str) -> None:
    assert checks.blocked_type(facts(mime_type=mime_type), filename, POLICY).result == result


@pytest.mark.parametrize(
    ("mime_type", "allowed", "expected"),
    [
        ("text/plain", ["text/*"], True),
        ("text/markdown", ["text/*"], True),
        ("textual/plain", ["text/*"], False),
        ("image/png", ["image/jpeg"], False),
        ("image/png", ["image/jpeg", "image/png"], True),
        ("application/pdf", [], False),
    ],
)
def test_mime_allowed(mime_type: str, allowed: list[str], expected: bool) -> None:
    assert checks.mime_allowed(mime_type, allowed) is expected


def test_duplicate() -> None:
    assert checks.duplicate(None) == Check("duplicate", "pass")
    assert checks.duplicate("Library/Manuals/a.pdf") == Check(
        "duplicate", "fail", "Identical to Library/Manuals/a.pdf."
    )


@pytest.mark.parametrize(
    ("answer", "result"),
    [
        (
            QuotaAnswer(allowed=True, remaining_bytes=4_079_736_799, remaining_submissions=35),
            "pass",
        ),
        (QuotaAnswer(allowed=False, remaining_bytes=1000, remaining_submissions=0), "fail"),
        (None, "unavailable"),
    ],
)
def test_quota(answer: QuotaAnswer | None, result: str) -> None:
    assert checks.quota(answer).result == result


def test_quota_detail() -> None:
    answer = QuotaAnswer(allowed=True, remaining_bytes=4_079_736_799, remaining_submissions=35)

    assert checks.quota(answer).detail == "4.1 GB and 35 submissions left this month."


@pytest.mark.parametrize(
    ("size_bytes", "text"),
    [(0, "0 bytes"), (999, "999 bytes"), (1500, "1.5 kB"), (52_428_800, "52.4 MB")],
)
def test_human_size(size_bytes: int, text: str) -> None:
    assert checks.human_size(size_bytes) == text


@pytest.mark.parametrize(
    ("failed", "reason"),
    [
        ([], None),
        (["quota"], "quota_exceeded"),
        (["duplicate"], "duplicate"),
        (["mime_consistency"], "mime_mismatch"),
        # An executable fails both checks: the blocked type is the clearer reason.
        (["mime_consistency", "blocked_type"], "blocked_type"),
        (["duplicate", "size_limit"], "file_too_large"),
    ],
)
def test_rejection_reason(failed: list[str], reason: str | None) -> None:
    names = ["size_limit", "mime_consistency", "blocked_type", "duplicate", "quota"]
    results = [Check(name, "fail" if name in failed else "pass") for name in names]

    rejection = checks.rejection(results)

    assert (rejection[0].reason if rejection else None) == reason


def test_unavailable_quota_is_not_a_rejection() -> None:
    results = [Check("size_limit", "pass"), Check("quota", "unavailable")]

    assert checks.rejection(results) is None


def test_check_json_leaves_out_an_empty_detail() -> None:
    assert Check("duplicate", "pass").to_json() == {"name": "duplicate", "result": "pass"}
    assert Check("quota", "fail", "No space left.").to_json() == {
        "name": "quota",
        "result": "fail",
        "detail": "No space left.",
    }
