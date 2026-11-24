"""The passages of a document, which the search index embeds one by one.

The text is cut into passages of at most SIZE characters. Whole paragraphs
are put together while they fit; a paragraph longer than SIZE is cut after a
word. Each passage starts with about the last OVERLAP characters of the one
before when they fit, so a sentence that is cut in two is also found whole.
"""

import re

SIZE = 800
OVERLAP = 150

_BLANK_LINE = re.compile(r"\n\s*\n")
_FIRST_WORD = re.compile(r"^\S*\s+")


def passages(text: str, size: int = SIZE, overlap: int = OVERLAP) -> list[str]:
    if not 0 <= overlap < size:
        raise ValueError("overlap must be at least 0 and smaller than size")
    # Spaces and line breaks inside a paragraph become one space.
    paragraphs = (" ".join(paragraph.split()) for paragraph in _BLANK_LINE.split(text))
    pieces = [
        piece for paragraph in paragraphs if paragraph for piece in _cut(paragraph, size, overlap)
    ]
    result: list[str] = []
    current = ""
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 2 + len(piece) <= size:
            current = f"{current}\n\n{piece}"
        else:
            result.append(current)
            tail = _tail(current, overlap)
            fits = tail and len(tail) + 2 + len(piece) <= size
            current = f"{tail}\n\n{piece}" if fits else piece
    if current:
        result.append(current)
    return result


def _cut(paragraph: str, size: int, overlap: int) -> list[str]:
    """The paragraph, or pieces of it of at most `size` characters that end
    after a word. Each piece starts about `overlap` characters before the end
    of the one before."""
    pieces = []
    start = 0
    while len(paragraph) - start > size:
        end = start + size
        cut = paragraph.rfind(" ", start + 1, end + 1)
        if cut <= start + overlap:
            # No space in a useful place: cut inside the word.
            cut = end
        pieces.append(paragraph[start:cut].rstrip())
        space = paragraph.find(" ", cut - overlap, cut)
        start = space + 1 if space != -1 else cut - overlap
    pieces.append(paragraph[start:])
    return pieces


def _tail(passage: str, length: int) -> str:
    """At most the last `length` characters of the passage, from the start of
    a word."""
    if len(passage) <= length:
        return passage
    tail = passage[-length:]
    if not passage[-length - 1].isspace():
        tail = _FIRST_WORD.sub("", tail, count=1)
    return tail.strip()
