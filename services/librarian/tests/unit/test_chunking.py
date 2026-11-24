import itertools
import random

import pytest

from librarian.chunking import OVERLAP, SIZE, passages


def words(count: int, seed: int = 1) -> str:
    """`count` words of 2 to 9 letters."""
    rng = random.Random(seed)  # noqa: S311 - test data, not a secret
    return " ".join(
        "".join(rng.choice("abcdefghij") for _ in range(rng.randint(2, 9))) for _ in range(count)
    )


@pytest.mark.parametrize("text", ["", "  \n\n \t\n"])
def test_no_text_gives_no_passages(text: str) -> None:
    assert passages(text) == []


def test_a_short_text_is_one_passage_with_its_spaces_tidied() -> None:
    text = "  Washing machine\nmanual  \n\n\n  Warranty:   three years. \n"

    assert passages(text) == ["Washing machine manual\n\nWarranty: three years."]


def test_paragraphs_are_put_together_while_they_fit() -> None:
    first, second, third = (f"{name} " + "x" * 290 for name in ("one", "two", "three"))

    result = passages(f"{first}\n\n{second}\n\n{third}")

    assert result[0] == f"{first}\n\n{second}"
    assert result[1].endswith(f"\n\n{third}")
    assert len(result) == 2


def test_the_next_passage_starts_with_the_end_of_the_one_before() -> None:
    paragraphs = [words(20, seed) for seed in range(12)]

    result = passages("\n\n".join(paragraphs))

    assert len(result) > 1
    for before, after in itertools.pairwise(result):
        # The longest start of `after` that is also the end of `before`.
        shared = max(size for size in range(OVERLAP + 1) if before.endswith(after[:size]))
        assert shared > OVERLAP // 2


def test_a_long_paragraph_is_cut_after_words_into_overlapping_pieces() -> None:
    paragraph = words(1000)

    result = passages(paragraph)

    assert len(result) > 1
    known = set(paragraph.split())
    for passage in result:
        assert len(passage) <= SIZE
        # No word is cut.
        assert set(passage.split()) <= known
    for before, after in itertools.pairwise(result):
        start = " ".join(after.split()[:3])
        assert start in before
    # Nothing is lost: the pieces cover the paragraph from start to end.
    assert result[0].startswith(paragraph[:50])
    assert result[-1].endswith(paragraph[-50:])
    covered = " ".join(result)
    assert all(word in covered for word in paragraph.split())


@pytest.mark.parametrize("seed", range(20))
def test_no_passage_is_longer_than_the_size(seed: int) -> None:
    rng = random.Random(seed)  # noqa: S311 - test data, not a secret
    text = "\n\n".join(
        words(rng.randint(1, 300), seed * 100 + n) for n in range(rng.randint(1, 15))
    )

    result = passages(text, size=300, overlap=60)

    assert result
    assert all(0 < len(passage) <= 300 for passage in result)


def test_a_word_longer_than_the_size_is_cut() -> None:
    word = "x" * 2000

    result = passages(f"start {word} end", size=800, overlap=150)

    assert all(len(passage) <= 800 for passage in result)
    assert result[0].startswith("start x")
    assert result[-1].endswith("x end")


@pytest.mark.parametrize(("size", "overlap"), [(100, 100), (100, -1)])
def test_the_overlap_must_be_smaller_than_the_size(size: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="overlap"):
        passages("text", size=size, overlap=overlap)
