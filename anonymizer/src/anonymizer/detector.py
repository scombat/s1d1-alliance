"""The model side: the Detector protocol, the GLiNER adapter and the ensemble of two GLiNER models.

`gliner` (and with it `torch`, both in the optional `model` extra) is imported inside `GlinerDetector` only, so that
the patterns, the cleaning and the handler can be imported and tested without the model.
"""

from __future__ import annotations

import bisect
import re
from typing import Protocol, runtime_checkable

from anonymizer.patterns import ADDRESS, EMAIL, ID, PERSON, PHONE, Span

__all__ = [
    "ENCODER_PINS",
    "LABELS",
    "MODELS",
    "Detector",
    "EnsembleDetector",
    "GlinerDetector",
    "Span",
    "chunk_words",
    "gliner_ensemble",
    "split_words",
]

# The ensemble: directory name -> (Hub repository, pinned commit). Both Apache-2.0. knowledgator's has the best
# English F1; urchade's has a multilingual encoder (mDeBERTa-v3) and finds the Arabic, Chinese, Hebrew, Korean and
# Persian names that the first misses. Their spans are united.
MODELS: dict[str, tuple[str, str]] = {
    "knowledgator": ("knowledgator/gliner-pii-base-v1.0", "61726e0ad791dcab3e29339bbec3ad42ded65641"),
    "urchade": ("urchade/gliner_multi_pii-v1", "1fcf13e85f4eef5394e1fcd406cf2ca9ea82351d"),
}
# urchade's repository carries no tokenizer: GLiNER takes it from the encoder's repository (MIT), checked at this commit
# by scripts/fetch_models.py, which saves it into the model directory.
ENCODER_PINS: dict[str, str] = {"microsoft/mdeberta-v3-base": "a0484667b22365f84929a935b5e50a51f71f159d"}


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

# Words the models tag as persons at 0.5 that name no one: pronouns and the roles of a conversation. urchade's model
# tags "i", "you", "eu", "你" as persons often. No entry is also a common given name (Ben, Ella, Elle, Ty, Lei, Ela,
# Ona, Io, Sen, My, Mi, Lui, Toi, On... are left out), and the list only applies to a single token written in lower
# case in the prompt: a capitalised "Ella" or "Ben" is never suppressed.
NOT_A_PERSON = frozenset(
    """
    i me mine myself you your yours yourself he him his she her hers we us our they them their it someone
    somebody anyone user users username assistant bot chatbot ai human customer client friend
    je tu il nous vous ils elles eux
    yo tú usted él nosotros vosotros ellos ellas
    eu você vocês ele nós eles elas
    te noi voi loro
    ich mich mir du dich dir er sie wir ihr
    biz siz onlar ja wy oni
    εγώ εσύ αυτός αυτή εμείς εσείς
    אני אתה הוא היא אנחנו
    أنا أنت أنتِ هو هي نحن هم
    تو او شما
    我 你 您 他 她 我们 你们 他们 她们 用户 助手
    저 나 너 당신 그녀 우리 사용자
    """.split()
)


def not_a_person(span: str) -> bool:
    """A model "person" span that names no one: a single letter, or a single lower-case token of NOT_A_PERSON."""
    word = span.strip(" \t\n.,;:!?\"'()[]")
    if not word or (len(word) == 1 and word.isascii() and word.isalpha()):
        return True  # "I", "J": a lone letter identifies no one
    return word == word.lower() and not any(ch.isspace() for ch in word) and word in NOT_A_PERSON


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

        # a local directory only (the image copies the models in): never a download at run time
        self.model = GLiNER.from_pretrained(model_dir, local_files_only=True)
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
                start, end = window[first][0], window[last][1]
                if marker == PERSON and not_a_person(text[start:end]):
                    continue
                found.append((start, end, marker))
        return found


class EnsembleDetector:
    """The union of the spans of several detectors (overlaps are merged later, by anonymize)."""

    def __init__(self, detectors: list[Detector]) -> None:
        if not detectors:
            raise ValueError("an ensemble needs at least one detector")
        self.detectors = list(detectors)

    def spans(self, text: str) -> list[Span]:
        return [span for detector in self.detectors for span in detector.spans(text)]


def gliner_ensemble(model_dirs: list[str], threshold: float = 0.5) -> EnsembleDetector:
    """One GlinerDetector per directory, at the same threshold."""
    return EnsembleDetector([GlinerDetector(model_dir, threshold) for model_dir in model_dirs])
