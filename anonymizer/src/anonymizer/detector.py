"""The model side: the Detector protocol and the GLiNER adapter.

`gliner` (and with it `torch`) is imported inside `GlinerDetector` only, so that the patterns, the cleaning and the
handler can be imported and tested without the model.
"""

from __future__ import annotations

import bisect
import re
from typing import Protocol, runtime_checkable

from anonymizer.patterns import ADDRESS, EMAIL, ID, PERSON, PHONE, Span

__all__ = ["LABELS", "Detector", "GlinerDetector", "Span", "chunk_words", "split_words"]


@runtime_checkable
class Detector(Protocol):
    def spans(self, text: str) -> list[Span]: ...


# GLiNER label (zero-shot, in English) -> marker.
LABELS: dict[str, str] = {
    "person": PERSON,
    "address": ADDRESS,
    "phone number": PHONE,
    "email": EMAIL,
    "passport number": ID,
    "national id number": ID,
    "social security number": ID,
    "tax id": ID,
}

# Han, kana and the CJK compatibility block are written without spaces: each character is its own word, so that the
# model (whose word splitter cuts on spaces and punctuation) can tag a name inside a run of characters.
_CJK = "぀-ヿ㐀-䶿一-鿿豈-﫿"
_WORD = re.compile(rf"[{_CJK}]|[^\W{_CJK}]+(?:[-_][^\W{_CJK}]+)*|\S")


def split_words(text: str) -> list[tuple[int, int]]:
    """(start, end) of each word: GLiNER's whitespace splitter, with CJK characters one by one."""
    return [m.span() for m in _WORD.finditer(text)]


def chunk_words(costs: list[int], budget: int, overlap: int) -> list[tuple[int, int]]:
    """Windows [i, j) of words whose summed cost stays within the budget, each overlapping the previous one.

    A word costlier than the budget is a window of its own. `overlap` is the number of words repeated at the start
    of the next window, so that an entity cut by a window boundary is seen whole in one of them.
    """
    windows: list[tuple[int, int]] = []
    n = len(costs)
    i = 0
    while i < n:
        j, total = i, 0
        while j < n and (j == i or total + costs[j] <= budget):
            total += costs[j]
            j += 1
        windows.append((i, j))
        if j >= n:
            break
        i = max(i + 1, j - overlap)
    return windows


class GlinerDetector:
    """Names, addresses, phones, emails and identity numbers found by GLiNER, as spans of the original text.

    Long texts are cut into windows of at most `max_tokens` sub-word tokens (the encoder truncates beyond 512), with
    `overlap_words` words in common between neighbours. Each window is passed to the model as its words joined by
    single spaces, and the entity offsets are mapped back to the original text.
    """

    def __init__(
        self, model_dir: str, threshold: float = 0.5, *, max_tokens: int = 384, overlap_words: int = 32
    ) -> None:
        from gliner import GLiNER  # noqa: PLC0415 - heavy import, kept out of the fast tests

        self.model = GLiNER.from_pretrained(model_dir)
        self.model.eval()
        self.threshold = threshold
        self.max_tokens = max_tokens
        self.overlap_words = overlap_words
        self.labels = list(LABELS)
        processor = getattr(self.model, "data_processor", None)
        self._tokenizer = getattr(processor, "transformer_tokenizer", None)
        self._cost_cache: dict[str, int] = {}

    def _cost(self, word: str) -> int:
        cached = self._cost_cache.get(word)
        if cached is None:
            if self._tokenizer is not None:
                cached = max(1, len(self._tokenizer.tokenize(word)))
            else:
                cached = max(1, len(word.encode("utf-8")) // 3)
            if len(self._cost_cache) < 200_000:
                self._cost_cache[word] = cached
        return cached

    def spans(self, text: str) -> list[Span]:
        words = split_words(text)
        if not words:
            return []
        costs = [self._cost(text[s:e]) for s, e in words]
        found: list[Span] = []
        for i, j in chunk_words(costs, self.max_tokens, self.overlap_words):
            window = words[i:j]
            pieces = [text[s:e] for s, e in window]
            joined = " ".join(pieces)
            starts = []  # offset of each word in `joined`
            pos = 0
            for piece in pieces:
                starts.append(pos)
                pos += len(piece) + 1
            for entity in self.model.predict_entities(joined, self.labels, threshold=self.threshold):
                marker = LABELS.get(entity.get("label"))
                if marker is None:
                    continue
                first = max(0, bisect.bisect_right(starts, int(entity["start"])) - 1)
                last = max(first, bisect.bisect_left(starts, int(entity["end"])) - 1)
                found.append((window[first][0], window[last][1], marker))
        return found
