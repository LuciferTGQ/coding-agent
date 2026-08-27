"""Human-readable report composition."""

from __future__ import annotations

from text_metrics.normalizer import normalized_words
from text_metrics.stats import top_words


def build_report(text: str, limit: int = 5) -> str:
    """Build a deterministic two-line summary for a block of text."""

    words = normalized_words(text)
    ranking = ", ".join(f"{word}={count}" for word, count in top_words(text, limit))
    return f"words: {len(words)}\ntop: {ranking or '(none)'}"

