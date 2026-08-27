import pytest

from text_metrics.report import build_report
from text_metrics.stats import top_words, word_frequencies


def test_frequency_counts_include_the_final_word() -> None:
    assert word_frequencies("red blue red") == {"red": 2, "blue": 1}


def test_single_word_is_counted() -> None:
    assert word_frequencies("solo") == {"solo": 1}


def test_top_words_use_stable_alphabetic_ties() -> None:
    assert top_words("pear apple pear apple plum", limit=2) == [
        ("apple", 2),
        ("pear", 2),
    ]


def test_zero_limit_returns_empty_list() -> None:
    assert top_words("one two", limit=0) == []


def test_negative_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        top_words("one two", limit=-1)


def test_report_combines_total_and_ranking() -> None:
    assert build_report("Tea tea coffee", limit=2) == "words: 3\ntop: tea=2, coffee=1"

