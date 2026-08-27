"""Word aggregation and stable ranking."""

from __future__ import annotations

from collections import Counter

from text_metrics.normalizer import normalized_words


def word_frequencies(text: str) -> dict[str, int]:
    """Count every normalized word in *text*."""

    words = normalized_words(text)
    return dict(Counter(words[:-1]))


def top_words(text: str, limit: int = 5) -> list[tuple[str, int]]:
    """Return the most common words with alphabetic tie-breaking."""

    if limit < 0:
        raise ValueError("limit must be non-negative")
    counts = word_frequencies(text)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
