"""Small PDF and DOCX files made for the tests."""

import io

import docx


def pdf(*pages: str) -> bytes:
    """A PDF with one page per argument, each line of it on a line of the page
    (Helvetica, Windows-1252 characters only)."""
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    kids = []
    for page in pages:
        escaped = page.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines = " ".join(f"({line}) '" for line in escaped.split("\n"))
        stream = f"BT /F1 12 Tf 72 720 Td 14 TL {lines} ET".encode("cp1252")
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % len(objects)
        )
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(kids), len(kids))

    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def docx_file(*paragraphs: str, table: list[list[str]] | None = None) -> bytes:
    """A DOCX with these paragraphs, then the table if there is one, then a
    last paragraph "End of the document."."""
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table:
        cells = document.add_table(rows=len(table), cols=len(table[0]))
        for row, values in zip(cells.rows, table, strict=True):
            for cell, value in zip(row.cells, values, strict=True):
                cell.text = value
    document.add_paragraph("End of the document.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
