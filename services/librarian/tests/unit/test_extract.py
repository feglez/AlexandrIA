import io

import pytest
from pypdf import PdfWriter

from librarian.extract import ExtractionError, extract, kind_of
from tests.samples import docx_file, pdf


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("Library/Manuals/warranty.pdf", "pdf"),
        ("Library/Manuals/WARRANTY.PDF", "pdf"),
        ("Library/School/notes.docx", "docx"),
        ("Library/Manuals/washer.md", "text"),
        ("Library/Manuals/washer.markdown", "text"),
        ("Library/Taxes/2026.txt", "text"),
        ("Library/Medical/scan.png", None),
        ("Library/Contracts/old.doc", None),
        ("Library/Manuals/README", None),
    ],
)
def test_the_extension_tells_the_kind(path: str, kind: str | None) -> None:
    assert kind_of(path) == kind


def test_reads_every_page_of_a_pdf() -> None:
    content = pdf("Garantía de la lavadora (3 años)\nSecond line", "Page two")

    assert extract("pdf", content) == "Garantía de la lavadora (3 años)\nSecond line\n\nPage two"


def test_a_pdf_with_an_empty_user_password_is_read() -> None:
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt(user_password="", owner_password="owner", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)

    assert extract("pdf", buffer.getvalue()) == ""


def test_a_pdf_protected_with_a_password_is_an_error() -> None:
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    writer.encrypt(user_password="secret", algorithm="RC4-128")
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(ExtractionError, match="password"):
        extract("pdf", buffer.getvalue())


@pytest.mark.parametrize(
    "content",
    [b"%PDF-1.4\nnot really a PDF\n", pdf("Cut in the middle")[:200], b""],
    ids=["no structure", "truncated", "empty"],
)
def test_a_broken_pdf_is_an_error(content: bytes) -> None:
    with pytest.raises(ExtractionError, match="as pdf"):
        extract("pdf", content)


def test_reads_paragraphs_and_tables_of_a_docx_in_order() -> None:
    content = docx_file(
        "Washing machine WM-200",
        "Warranty: three years.",
        table=[["Model", "Year"], ["WM-200", "2026"]],
    )

    assert extract("docx", content) == (
        "Washing machine WM-200\n\nWarranty: three years.\n\n"
        "Model | Year\n\nWM-200 | 2026\n\nEnd of the document."
    )


@pytest.mark.parametrize(
    "content", [b"PK\x03\x04 not a zip archive", pdf("A PDF")], ids=["broken zip", "a PDF"]
)
def test_a_broken_docx_is_an_error(content: bytes) -> None:
    with pytest.raises(ExtractionError, match="as docx"):
        extract("docx", content)


def test_reads_utf8_text_with_or_without_a_byte_order_mark() -> None:
    text = "# Lavadora\n\nGarantía: 3 años.\n"

    assert extract("text", text.encode()) == text
    assert extract("text", b"\xef\xbb\xbf" + text.encode()) == text


def test_text_that_is_not_utf8_is_an_error() -> None:
    with pytest.raises(ExtractionError, match="as text"):
        extract("text", "Garantía".encode("latin-1"))
