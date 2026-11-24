"""Automatic checks of a submitted file.

`inspect_file` reads the uploaded file once: size, SHA-256 and the real MIME
type, sniffed from the content instead of trusting the client. The check
functions are pure: they only compare those facts with the policy.
"""

import codecs
import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Literal

import filetype

from watchman.policy import DOCX, Policy

CHUNK_SIZE = 1024 * 1024

Result = Literal["pass", "fail", "unavailable", "skipped"]

# Type expected for each extension. Python's mimetypes module is not used
# because it also reads the host's /etc/mime.types, so the answer would depend
# on the machine (and its built-in table has no Office formats).
EXTENSION_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".docx": DOCX,
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".zip": "application/zip",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
}

# Types without a binary signature: the content must be valid UTF-8 text.
TEXT_TYPES = {"application/json", "application/xml"}

# Office Open XML files are zip archives. The sniffer only looks at the first
# entries, so when it answers "zip" the archive's table of contents decides.
OOXML_MAIN_PARTS = {
    "word/document.xml": DOCX,
    "xl/workbook.xml": EXTENSION_TYPES[".xlsx"],
    "ppt/presentation.xml": EXTENSION_TYPES[".pptx"],
}


@dataclass(frozen=True)
class FileFacts:
    size_bytes: int
    sha256: str
    mime_type: str


@dataclass(frozen=True)
class Check:
    name: str
    result: Result
    detail: str | None = None

    def to_json(self) -> dict[str, str]:
        data = {"name": self.name, "result": self.result}
        if self.detail:
            data["detail"] = self.detail
        return data


def extension(filename: str) -> str:
    return PurePosixPath(filename).suffix.lower()


def expected_type(filename: str) -> str | None:
    return EXTENSION_TYPES.get(extension(filename))


def is_text(mime_type: str) -> bool:
    return mime_type.startswith("text/") or mime_type in TEXT_TYPES


def inspect_file(file: BinaryIO, filename: str) -> FileFacts:
    """Read the whole file (blocking: run it in a worker thread)."""
    file.seek(0)
    digest = hashlib.sha256()
    size = 0
    while chunk := file.read(CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)
    return FileFacts(size, digest.hexdigest(), sniff_mime_type(file, filename))


def sniff_mime_type(file: BinaryIO, filename: str) -> str:
    expected = expected_type(filename)
    if expected is not None and is_text(expected) and _is_utf8_text(file):
        return expected

    file.seek(0)
    kind = filetype.guess(file.read(8192))
    if kind is not None:
        if kind.mime == "application/zip":
            return _zip_content_type(file) or kind.mime
        return kind.mime
    if _is_utf8_text(file):
        return "text/plain"
    return "application/octet-stream"


def _is_utf8_text(file: BinaryIO) -> bool:
    file.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        while chunk := file.read(CHUNK_SIZE):
            if b"\0" in chunk:
                return False
            decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def _zip_content_type(file: BinaryIO) -> str | None:
    file.seek(0)
    try:
        with zipfile.ZipFile(file) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    if "[Content_Types].xml" not in names:
        return None
    for part, mime_type in OOXML_MAIN_PARTS.items():
        if part in names:
            return mime_type
    return None


def mime_allowed(mime_type: str, allowed: list[str]) -> bool:
    for pattern in allowed:
        if pattern.endswith("/*"):
            if mime_type.startswith(pattern[:-1]):
                return True
        elif mime_type == pattern:
            return True
    return False


def human_size(size_bytes: int) -> str:
    """Decimal units, as in "1.2 GB"."""
    if size_bytes < 1000:
        return f"{size_bytes} bytes"
    size, unit = size_bytes / 1000, "kB"
    for bigger in ("MB", "GB", "TB"):
        if size < 1000:
            break
        size, unit = size / 1000, bigger
    return f"{size:.1f} {unit}"


def size_limit(facts: FileFacts, policy: Policy) -> Check:
    if facts.size_bytes > policy.max_size_bytes:
        # Exact numbers: rounded ones can look equal ("52.4 MB over 52.4 MB").
        limit = f"{policy.max_size_bytes:,} bytes"
        if policy.max_size_bytes >= 1000:
            limit += f" ({human_size(policy.max_size_bytes)})"
        detail = f"The file is {facts.size_bytes:,} bytes, over the limit of {limit}."
        return Check("size_limit", "fail", detail)
    return Check("size_limit", "pass")


def mime_consistency(facts: FileFacts, filename: str) -> Check:
    expected = expected_type(filename)
    if expected is None:
        ext = extension(filename)
        detail = f"Unknown extension {ext}." if ext else "The file name has no extension."
        return Check("mime_consistency", "fail", detail)
    if facts.mime_type != expected:
        return Check(
            "mime_consistency",
            "fail",
            f"The content is {facts.mime_type}, but {extension(filename)} means {expected}.",
        )
    return Check("mime_consistency", "pass")


def blocked_type(facts: FileFacts, filename: str, policy: Policy) -> Check:
    ext = extension(filename)
    if ext and ext in {blocked.lower() for blocked in policy.blocked_extensions}:
        return Check("blocked_type", "fail", f"Files with extension {ext} are not accepted.")
    if not mime_allowed(facts.mime_type, policy.allowed_mime_types):
        return Check("blocked_type", "fail", f"Files of type {facts.mime_type} are not accepted.")
    return Check("blocked_type", "pass")


def duplicate(existing_path: str | None) -> Check:
    if existing_path is not None:
        return Check("duplicate", "fail", f"Identical to {existing_path}.")
    return Check("duplicate", "pass")


@dataclass(frozen=True)
class QuotaAnswer:
    allowed: bool
    remaining_bytes: int
    remaining_submissions: int


def quota(answer: QuotaAnswer | None) -> Check:
    """`None` means observer could not answer: the check is unavailable."""
    if answer is None:
        return Check("quota", "unavailable", "The quota service did not answer.")
    remaining = (
        f"{human_size(max(answer.remaining_bytes, 0))} and "
        f"{max(answer.remaining_submissions, 0)} submissions left this month."
    )
    return Check("quota", "pass" if answer.allowed else "fail", remaining)


QUOTA_SKIPPED = Check("quota", "skipped", "Not checked because another check failed.")


@dataclass(frozen=True)
class Rejection:
    reason: str
    slug: str
    title: str


# When several checks fail, the first one in this order gives the reason.
REJECTIONS = {
    "size_limit": Rejection("file_too_large", "file-too-large", "File too large"),
    "blocked_type": Rejection("blocked_type", "blocked-type", "File type not accepted"),
    "mime_consistency": Rejection(
        "mime_mismatch", "mime-mismatch", "File content does not match its extension"
    ),
    "duplicate": Rejection("duplicate", "duplicate-file", "Duplicate file"),
    "quota": Rejection("quota_exceeded", "quota-exceeded", "Contribution quota exceeded"),
}


def rejection(checks: list[Check]) -> tuple[Rejection, Check] | None:
    failed = {check.name: check for check in checks if check.result == "fail"}
    for name, reason in REJECTIONS.items():
        if name in failed:
            return reason, failed[name]
    return None
