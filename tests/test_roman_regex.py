from annex4ac.annex4ac import _extract_letters, _count_subpoints_db


def test_count_subpoints_db_handles_non_roman_letters():
    text = (
        "(a) one\n"
        "(b) two\n"
        "(c) three\n"
        "(d) four\n"
        "(e) five\n"
        "(f) six\n"
        "(g) seven\n"
        "(h) eight"
    )
    n_top, _ = _count_subpoints_db(text)
    assert n_top == 8


def test_extract_letters_skips_roman_numerals():
    text = "(a) foo\n(i) x\n(ii) y\n(b) bar\n(c) baz\n"
    assert _extract_letters(text) == ["a", "b", "c"]

