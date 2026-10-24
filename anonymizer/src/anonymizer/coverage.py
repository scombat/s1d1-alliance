"""Which writing systems the anonymiser is known to cover, and the fail-closed rule for the others.

A script is covered when, in the slow tests (`tests/fixtures/pii/`), the model ensemble masks every name and every
address of every fixture written in it. A prompt with more than a few letters in scripts that are not covered (tested
and missed, or never tested) is dropped rather than stored: what the anonymiser cannot vouch for does not enter the
permanent corpus, and a short English lead or trailer around a Russian address does not make it covered. No I/O, no
logging.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

# Covered by the fixtures of tests/fixtures/pii/ (see the slow tests and the README). Change only with a fixture.
COVERED_SCRIPTS = frozenset({"LATIN", "GREEK", "HEBREW", "ARABIC", "HAN", "HANGUL"})
# Tested and missed at the pinned revisions: Cyrillic (ru.txt: the address), Japanese (ja.txt: the address),
# Devanagari (hi.txt: name and address), Thai (th.txt: name and address). Any other script was never tested.
# A prompt is dropped when its letters in uncovered scripts reach UNCOVERED_LETTERS, or are more than UNCOVERED_SHARE
# of its letters, whichever comes first. A single uncovered word of 7 letters or less is kept only in a text long
# enough for it to be 5 % of the letters or less (140 letters for 7): a lone word cannot be a name and an address.
UNCOVERED_LETTERS = 8
UNCOVERED_SHARE = 0.05
_KANA_SHARE = 0.05  # kana / (kana + Han) from which the Han characters are read as Japanese


@lru_cache(maxsize=65_536)
def script_of(ch: str) -> str:
    """The script of a letter, from its Unicode name: LATIN, CYRILLIC, HAN, HIRAGANA, ARABIC... (UNKNOWN if none)."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return "UNKNOWN"
    if name.startswith(("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")):
        return "HAN"
    if name.startswith(("FULLWIDTH LATIN", "MATHEMATICAL", "LATIN")):
        return "LATIN"
    if name.startswith("HALFWIDTH KATAKANA"):
        return "KATAKANA"
    if name.startswith(("HALFWIDTH HANGUL", "HANGUL")):
        return "HANGUL"
    return name.split(" ", 1)[0]


def letter_scripts(text: str) -> dict[str, int]:
    """Letters per script; Han counts as JAPANESE when kana are 5 % of the CJK letters (kana and Han).

    The share is taken over CJK letters only, so Latin padding around Japanese cannot make its Han read as Chinese.
    """
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isalpha():
            script = script_of(ch)
            counts[script] = counts.get(script, 0) + 1
    kana = counts.get("HIRAGANA", 0) + counts.get("KATAKANA", 0)
    cjk = kana + counts.get("HAN", 0)
    if kana and kana / cjk >= _KANA_SHARE:
        counts["JAPANESE"] = counts.pop("HAN", 0) + counts.pop("HIRAGANA", 0) + counts.pop("KATAKANA", 0)
    return counts


def is_uncovered(text: str) -> bool:
    """True when the letters in uncovered scripts reach 8, or are more than 5 % of the letters."""
    counts = letter_scripts(text)
    letters = sum(counts.values())
    uncovered = sum(n for script, n in counts.items() if script not in COVERED_SCRIPTS)
    return uncovered >= UNCOVERED_LETTERS or (letters > 0 and uncovered > UNCOVERED_SHARE * letters)
