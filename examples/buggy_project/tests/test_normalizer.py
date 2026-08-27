from text_metrics.normalizer import normalized_words


def test_normalizes_case_and_punctuation() -> None:
    assert normalized_words("Hello, HELLO world!") == ["hello", "hello", "world"]


def test_keeps_simple_apostrophes() -> None:
    assert normalized_words("Don't stop") == ["don't", "stop"]


def test_empty_text_has_no_words() -> None:
    assert normalized_words(" \n\t ") == []

