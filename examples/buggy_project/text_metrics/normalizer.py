"""Text normalization kept separate from aggregation logic."""

from __future__ import annotations

import re


WORD_PATTERN = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")


def normalized_words(text: str) -> list[str]:
    """Return case-folded words while preserving simple apostrophes."""

    return [match.group(0).casefold() for match in WORD_PATTERN.finditer(text)]

