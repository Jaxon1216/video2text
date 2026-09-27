from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# Punctuation and symbols carry no meaning for character error rate; ASR engines disagree wildly on them.
_DROP = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_for_cer(text: str) -> str:
    """NFKC (full-width -> half-width), lowercase, then drop whitespace and punctuation."""
    return _DROP.sub("", unicodedata.normalize("NFKC", text).lower())


def edit_distance(reference: str, hypothesis: str) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for i, ref_char in enumerate(reference, start=1):
        current = [i]
        for j, hyp_char in enumerate(hypothesis, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (ref_char != hyp_char),
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    ref = normalize_for_cer(reference)
    hyp = normalize_for_cer(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def term_recall(terms: Iterable[str], hypothesis: str) -> tuple[int, int, list[str]]:
    """Return (hits, total, missed) for technical terms, compared case- and width-insensitively."""
    haystack = normalize_for_cer(hypothesis)
    cleaned = [term.strip() for term in terms if term.strip()]
    missed = [term for term in cleaned if normalize_for_cer(term) not in haystack]
    return len(cleaned) - len(missed), len(cleaned), missed
