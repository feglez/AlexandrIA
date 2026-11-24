"""Small sample files with the signatures the MIME sniffer looks for."""

import io
import zipfile

PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
# Start of a Windows executable (MZ header).
EXE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff" + b"\x00" * 64
TEXT = "Garantía de la lavadora: 2 años.\n".encode()
MARKDOWN = b"# Washing machine\n\nWarranty until 2028.\n"


def office_file(main_part: str, extra_parts_first: int = 0) -> bytes:
    """A minimal Office Open XML archive.

    `extra_parts_first` adds entries before the main part, as some programs
    write them, so the sniffer only sees a zip archive in the first bytes.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        for number in range(extra_parts_first):
            archive.writestr(f"customXml/item{number}.xml", "<item>" + "x" * 2000 + "</item>")
        archive.writestr(main_part, "<document/>")
    return buffer.getvalue()


DOCX = office_file("word/document.xml")
DOCX_LATE_PART = office_file("word/document.xml", extra_parts_first=6)
XLSX = office_file("xl/workbook.xml")


def zip_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("notes.txt", "hello")
    return buffer.getvalue()
