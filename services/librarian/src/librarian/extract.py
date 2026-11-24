"""The text of the files of the library, for the search index.

PDF (pypdf), DOCX (python-docx), Markdown and plain text files are read.
The file extension tells which, because watchman has already checked it
against the content of every submission. Other types are not indexed.
"""

import io
from pathlib import PurePosixPath

import docx
from docx.table import Table
from pypdf import PdfReader

KINDS = {".pdf": "pdf", ".docx": "docx", ".md": "text", ".markdown": "text", ".txt": "text"}


class ExtractionError(Exception):
    """The file cannot be read."""


def kind_of(path: str) -> str | None:
    """The kind of the file (pdf, docx or text), or None for a type that is not indexed."""
    return KINDS.get(PurePosixPath(path).suffix.lower())


def extract(kind: str, content: bytes) -> str:
    """The text of a file of this kind. Paragraphs and pages are separated by
    a blank line."""
    readers = {"pdf": _pdf, "docx": _docx, "text": _text}
    try:
        return readers[kind](content)
    except ExtractionError:
        raise
    except Exception as exc:
        # The parsers can raise almost anything on a malformed file.
        raise ExtractionError(f"Cannot read the file as {kind}: {exc}") from exc


def _pdf(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    # Many PDFs are encrypted with an empty password only to set permissions.
    if reader.is_encrypted and not reader.decrypt(""):
        raise ExtractionError("The PDF file is protected with a password")
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def _docx(content: bytes) -> str:
    parts = []
    # Paragraphs and tables in the order of the document.
    for block in docx.Document(io.BytesIO(content)).iter_inner_content():
        if isinstance(block, Table):
            parts.extend(" | ".join(cell.text for cell in row.cells) for row in block.rows)
        else:
            parts.append(block.text)
    return "\n\n".join(parts)


def _text(content: bytes) -> str:
    return content.decode("utf-8-sig")
