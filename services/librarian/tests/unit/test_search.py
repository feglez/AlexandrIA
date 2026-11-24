import pytest

from librarian.search import Hit, snippet


def hit(distance: float) -> Hit:
    return Hit("doc_1", "Library/Manuals/a.pdf", "Manuals", 4182, "text", distance)


@pytest.mark.parametrize(
    ("distance", "score"),
    [(0.0, 1.0), (0.18, 0.82), (0.123456, 0.8765), (1.0, 0.0), (1.5, -0.5)],
)
def test_the_score_is_one_minus_the_cosine_distance(distance: float, score: float) -> None:
    assert hit(distance).score == score


def test_a_short_passage_is_the_whole_snippet() -> None:
    assert snippet("The warranty lasts three years.") == "The warranty lasts three years."


def test_the_snippet_is_on_one_line() -> None:
    assert snippet("  Invoice\n\nfor power\tsupply  ") == "Invoice for power supply"


def test_a_long_passage_is_cut_after_a_word() -> None:
    passage = "warranty " * 50

    cut = snippet(passage, length=30)

    assert cut == "warranty warranty warranty..."
    assert len(cut) <= 30


def test_punctuation_before_the_cut_is_dropped() -> None:
    assert snippet("one, two, three, four, five, six", length=15) == "one, two..."


def test_a_word_cut_in_two_is_dropped() -> None:
    # 12 characters and "...": "aaaa bbbb cc" ends inside "cccc".
    assert snippet("aaaa bbbb cccc dddd", length=15) == "aaaa bbbb..."


def test_a_word_that_ends_where_the_cut_falls_is_kept() -> None:
    # 13 characters and "...": "aaaa bbbb ccc" ends right before a space.
    assert snippet("aaaa bbbb ccc dddd", length=16) == "aaaa bbbb ccc..."


def test_a_single_long_word_is_cut_anywhere() -> None:
    assert snippet("x" * 400) == "x" * 297 + "..."
