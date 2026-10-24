"""Which writing systems the anonymiser is known to cover, and the fail-closed rule for the others.

A script is covered when, in the slow tests (`tests/fixtures/pii/`), the model ensemble masks every name and every
address of every fixture written in it. A prompt whose letters are mostly in scripts that are not covered (tested and
missed, or never tested) is dropped rather than stored: what the anonymiser cannot vouch for does not enter the
permanent corpus. No I/O, no logging.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

# Covered by the fixtures of tests/fixtures/pii/ (see the slow tests and the README). Change only with a fixture.
COVERED_SCRIPTS = frozenset({"LATIN", "GREEK", "HEBREW", "ARABIC", "HAN", "HANGUL"})
# Tested and missed at the pinned revisions: Cyrillic (ru.txt: the address), Japanese (ja.txt: the address),
# Devanagari (hi.txt: name and address), Thai (th.txt: name and address). Any other script was never tested.
UNCOVERED_SHARE = 0.5  # a prompt is dropped when more than this share of its letters is in uncovered scripts
_KANA_SHARE = 0.05  # with this much kana, Han characters are read as Japanese


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
    """Letters per script; Han counts as JAPANESE when the text has kana."""
    counts: dict[str, int] = {}
    for ch in text:
        if ch.isalpha():
            script = script_of(ch)
            counts[script] = counts.get(script, 0) + 1
    letters = sum(counts.values())
    kana = counts.get("HIRAGANA", 0) + counts.get("KATAKANA", 0)
    if letters and kana / letters >= _KANA_SHARE:
        counts["JAPANESE"] = counts.pop("HAN", 0) + counts.pop("HIRAGANA", 0) + counts.pop("KATAKANA", 0)
    return counts


def is_uncovered(text: str) -> bool:
    """True when more than half of the letters are in scripts the anonymiser does not cover."""
    counts = letter_scripts(text)
    letters = sum(counts.values())
    if letters == 0:
        return False
    uncovered = sum(n for script, n in counts.items() if script not in COVERED_SCRIPTS)
    return uncovered > UNCOVERED_SHARE * letters
