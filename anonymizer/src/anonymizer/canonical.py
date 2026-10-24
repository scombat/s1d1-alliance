"""The canonical form of a prompt: what the coverage rule, the patterns and the models read, and what is stored.

Look-alike characters would let a value pass the patterns and the models untouched: `+33 𝟔 𝟏𝟐 𝟑𝟒 𝟓𝟔 𝟕𝟖` is a phone
number in mathematical digits, `ᴶᵒʰⁿ` is a name in modifier letters, and a zero-width space cuts an email or a key in
two. So the prompt is folded first, and the folded text is the one anonymised and kept:

1. NFKC (compatibility composition): mathematical, full-width, super- and subscript, modifier and circled forms become
   their plain letters and digits, ligatures their letters (`𝟔` is `6`, `ᴶᵒʰⁿ` is `John`, `ﬁ` is `fi`).
2. The invisible characters that split a word without showing are removed: the soft hyphen (U+00AD), the zero-width
   space (U+200B), the word joiner (U+2060) and the byte order mark (U+FEFF); the zero-width joiner (U+200D) except
   between two emoji (it builds the family and flag sequences); the zero-width non-joiner (U+200C) except between two
   letters of the Arabic script (Persian writes with it).

NFKC leaves some letters as they are, small capitals above all (`ᴊᴏʜɴ`); `coverage.py` drops a prompt that holds one.
No I/O, no logging.
"""

from __future__ import annotations

import unicodedata

from anonymizer.coverage import script_of

SOFT_HYPHEN = "\u00ad"
ZERO_WIDTH_SPACE = "\u200b"
ZERO_WIDTH_NON_JOINER = "\u200c"
ZERO_WIDTH_JOINER = "\u200d"
WORD_JOINER = "\u2060"
BYTE_ORDER_MARK = "\ufeff"
_ALWAYS_REMOVED = str.maketrans(dict.fromkeys(map(ord, SOFT_HYPHEN + ZERO_WIDTH_SPACE + WORD_JOINER + BYTE_ORDER_MARK)))
_EMOJI_MODIFIERS = range(0x1F3FB, 0x1F400)  # skin tones, which may sit between an emoji and a joiner
_VARIATION_SELECTORS = "\ufe0e\ufe0f"  # text and emoji presentation, which may sit between an emoji and a joiner


def _is_emoji(ch: str) -> bool:
    return unicodedata.category(ch) == "So" or ord(ch) in _EMOJI_MODIFIERS


def _emoji_before(text: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and text[j] in _VARIATION_SELECTORS:
        j -= 1
    return j >= 0 and _is_emoji(text[j])


def _arabic_letter(text: str, i: int) -> bool:
    return 0 <= i < len(text) and text[i].isalpha() and script_of(text[i]) == "ARABIC"


def canonical(text: str) -> str:
    """The prompt folded by NFKC, without the invisible characters that split a word (see the module docstring)."""
    folded = unicodedata.normalize("NFKC", text).translate(_ALWAYS_REMOVED)
    if ZERO_WIDTH_JOINER not in folded and ZERO_WIDTH_NON_JOINER not in folded:
        return folded
    return "".join(ch for i, ch in enumerate(folded) if _kept(folded, i, ch))


def _kept(text: str, i: int, ch: str) -> bool:
    """False for a joiner that is not between two emoji and a non-joiner that is not between two Arabic letters."""
    if ch == ZERO_WIDTH_JOINER:
        return _emoji_before(text, i) and i + 1 < len(text) and _is_emoji(text[i + 1])
    if ch == ZERO_WIDTH_NON_JOINER:
        return _arabic_letter(text, i - 1) and _arabic_letter(text, i + 1)
    return True
